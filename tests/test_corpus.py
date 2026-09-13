from collections import Counter

from orbitmesh.corpus import chunk_document, classify_product, load_manifest

from .conftest import CORPUS


def test_every_manifest_document_yields_chunks(chunks):
    per_doc = Counter(c.source_id for c in chunks)
    for meta in load_manifest(CORPUS):
        assert per_doc[meta.source_id] >= 1, meta.source_id


def test_product_line_comes_from_the_applies_to_line(chunks):
    by = {c.source_id: c for c in chunks}
    assert (by["pro-led-reference"].product_line, by["pro-led-reference"].product_explicit) == ("pro", True)
    assert (by["pro-quick-start-guide"].product_line, by["pro-quick-start-guide"].product_explicit) == ("pro", True)
    assert (by["troubleshooting-guide"].product_line, by["troubleshooting-guide"].product_explicit) == ("home", True)
    # No "Applies to" line -> inferred, therefore soft.
    assert by["warranty-safety-policy"].product_explicit is False


def test_archived_document_is_flagged(chunks):
    archived = {c.source_id for c in chunks if c.archived}
    assert archived == {"firmware-archive"}


def test_tables_are_not_split_across_chunks(chunks):
    n1 = [c for c in chunks if c.source_id == "led-reference" and c.locator == "N1 node LEDs"]
    assert len(n1) == 1
    assert "Flashing amber" in n1[0].text and "Mesh signal is too weak" in n1[0].text
    assert "Solid red" in n1[0].text  # the last table row is still there


def test_faq_questions_become_sections(chunks):
    faq = [c.locator for c in chunks if c.source_id == "customer-faq"]
    assert "What does a factory reset do?" in faq
    assert "Can I plug an N1 straight into my modem?" in faq


def test_chunk_ids_are_deterministic_and_change_with_content(tmp_path):
    meta = load_manifest(CORPUS)[0]
    a = chunk_document(meta)
    b = chunk_document(meta)
    assert [c.chunk_id for c in a] == [c.chunk_id for c in b]
    edited = tmp_path / meta.path.name
    edited.write_text(meta.path.read_text().replace("within 2 metres", "within 3 metres"))
    meta2 = type(meta)(source_id=meta.source_id, title=meta.title, path=edited, version="9.9",
                       effective_date="2027-01-01")
    c = chunk_document(meta2)
    changed = {x.chunk_id for x in c} ^ {x.chunk_id for x in a}
    assert len(changed) == 2  # exactly one section differs: one old id out, one new id in


def test_classify_product_edge_cases():
    assert classify_product("**Applies to:** OrbitMesh Pro R5 Pro and N5 Pro.") == ("pro", True)
    assert classify_product("nothing about hardware") == ("all", False)
    assert classify_product("Use one R1 and up to five N1 nodes.") == ("home", False)
