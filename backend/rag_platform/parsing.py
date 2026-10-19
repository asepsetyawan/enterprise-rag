"""Local structure-aware parsing, bounded chunks and immutable source-side cache."""
import hashlib
import io
import json
import os
import tempfile
from importlib.metadata import version

from .config import settings
from .storage import file_path


def content_hash(content):
    return hashlib.sha256(content.encode()).hexdigest()


def parser_configuration():
    cfg = settings()
    return {"parser_revision": cfg.parser_revision, "docling_version": version("docling"),
            "docling_core_version": version("docling-core"), "chunk_size": cfg.chunk_size,
            "tokenizer": "cl100k_base", "ocr_languages": cfg.parser_ocr_languages,
            "max_text_chars": cfg.max_text_chars}


def convert_document(path, configuration):
    if path.suffix == ".txt":
        from docling_core.types.doc import DocItemLabel, DoclingDocument
        document = DoclingDocument(name=path.stem)
        document.add_text(label=DocItemLabel.TEXT, text=path.read_text(encoding="utf-8-sig"))
        return document
    from docling.datamodel.accelerator_options import AcceleratorDevice, AcceleratorOptions
    from docling.datamodel.backend_options import MarkdownBackendOptions
    from docling.datamodel.base_models import ConversionStatus, DocumentStream, InputFormat
    from docling.datamodel.pipeline_options import OcrMode, PdfPipelineOptions, TesseractCliOcrOptions
    from docling.document_converter import DocumentConverter, MarkdownFormatOption, PdfFormatOption

    # Remote services and external plugins stay disabled. Models are provisioned offline.
    options = PdfPipelineOptions(
        artifacts_path=settings().parser_artifacts_path,
        accelerator_options=AcceleratorOptions(device=AcceleratorDevice.CPU, num_threads=2),
        do_ocr=True, do_table_structure=True, enable_remote_services=False, allow_external_plugins=False,
        ocr_options=TesseractCliOcrOptions(lang=configuration["ocr_languages"].split(","),
                                          mode=OcrMode.PDF_AWARE_LAYOUT_REGIONS),
    )
    converter = DocumentConverter(allowed_formats=[InputFormat.PDF, InputFormat.DOCX, InputFormat.MD],
                                  format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options),
                                                  InputFormat.MD: MarkdownFormatOption(backend_options=MarkdownBackendOptions(
                                                      fetch_images=False, enable_local_fetch=False, enable_remote_fetch=False))})
    source = path
    if path.suffix == ".md":
        source = DocumentStream(name="document.md", stream=io.BytesIO(path.read_bytes()))
    result = converter.convert(source, max_num_pages=1000, max_file_size=settings().max_upload_bytes)
    if result.status != ConversionStatus.SUCCESS:
        raise ValueError("Document conversion incomplete")
    return result.document


def chunk_document(document, configuration):
    import tiktoken
    from docling.chunking import HybridChunker
    from docling_core.transforms.chunker.tokenizer.base import BaseTokenizer
    from lightrag.utils import sanitize_text_for_encoding

    encoder = tiktoken.get_encoding(configuration["tokenizer"])
    class LiteralTokenizer(BaseTokenizer):
        # Documents may contain token-like strings; count them as ordinary text.
        def count_tokens(self, text):
            return len(encoder.encode_ordinary(text))

        def get_max_tokens(self):
            return configuration["chunk_size"]

        def get_tokenizer(self):
            return self.count_tokens

    chunker = HybridChunker(tokenizer=LiteralTokenizer(),
                            merge_peers=True, repeat_table_header=True, always_emit_headings=True)
    chunks = []
    metadata = {}
    total = 0
    for chunk in chunker.chunk(dl_doc=document):
        content = sanitize_text_for_encoding(chunker.contextualize(chunk))
        if not content:
            continue
        if len(encoder.encode(content, disallowed_special=())) > configuration["chunk_size"]:
            raise ValueError("Structured chunk exceeds token budget")
        total += len(content)
        if total > configuration["max_text_chars"] * 2 or len(chunks) >= 10000:
            raise ValueError("Structured chunks exceed document budget")
        pages = sorted({prov.page_no for item in chunk.meta.doc_items for prov in item.prov})
        headings = list(chunk.meta.headings or [])
        key = content_hash(content)
        if key not in metadata:
            chunks.append(content)
            metadata[key] = {"headings": headings, "pages": pages}
        else:
            # Identical text may appear on multiple pages; keep all valid locations.
            existing = metadata[key]
            existing["pages"] = sorted(set(existing["pages"]) | set(pages))
            existing["headings"] = list(dict.fromkeys(existing["headings"] + headings))
    if not chunks:
        raise ValueError("No usable document content")
    text = sanitize_text_for_encoding(document.export_to_markdown())
    if not text or len(text) > configuration["max_text_chars"]:
        raise ValueError("Extracted text exceeds document budget")
    # Spread the catalog budget across the whole document rather than truncating its tail.
    headings = list(dict.fromkeys(h for meta in metadata.values() for h in meta["headings"]))
    heading_text = "\n".join(headings)[:20000]
    per_chunk = max(1, min(1500, (180000 - len(heading_text)) // len(chunks)))
    catalog_text = heading_text + "\n" + "\n".join(part[:per_chunk] for part in chunks)
    return {"text": text, "chunks": chunks, "chunk_metadata": metadata, "catalog_text": catalog_text}


def parse_document(relative, configuration):
    if configuration["docling_version"] != version("docling") or configuration["docling_core_version"] != version("docling-core"):
        raise ValueError("Parser dependency changed; reindex with the current profile")
    path = file_path(relative)
    source_digest = hashlib.sha256(path.read_bytes()).hexdigest()
    identity = content_hash(json.dumps({"source": source_digest, "configuration": configuration}, sort_keys=True))
    cache = path.with_name(path.name + f".{identity}.parsed.json")
    if cache.exists():
        result = json.loads(cache.read_text())
        return {**result, "cache_hit": True}
    result = {**chunk_document(convert_document(path, configuration), configuration),
              "parser_configuration": configuration, "cache_hit": False}
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False, encoding="utf-8") as stream:
            temporary = stream.name
            os.chmod(temporary, 0o600)
            json.dump(result, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, cache)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)
    return result


def delete_parse_cache(relative):
    path = file_path(relative)
    for cache in path.parent.glob(path.name + ".*.parsed.json"):
        cache.unlink(missing_ok=True)


def main():
    """Download only layout/table models; OCR uses local Tesseract language data."""
    from docling.utils.model_downloader import download_models
    download_models(output_dir=settings().parser_artifacts_path, progress=True,
                    with_layout=True, with_tableformer=True, with_code_formula=False,
                    with_picture_classifier=False, with_smolvlm=False, with_granite_vision=False,
                    with_easyocr=False, with_rapidocr=False)


if __name__ == "__main__":
    main()
