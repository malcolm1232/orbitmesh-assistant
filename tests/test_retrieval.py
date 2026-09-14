"""Retrieval must respect the customer's product line and prefer current documents."""


def _sources(hits):
    return [h.chunk.source_id for h in hits]


def test_home_query_excludes_explicit_pro_documents(retriever):
    hits = retriever.retrieve("N1 node flashing amber keeps disconnecting", product_line="home")
    assert hits
    assert "pro-led-reference" not in _sources(hits)
    assert "pro-quick-start-guide" not in _sources(hits)
    assert _sources(hits)[0] in {"troubleshooting-guide", "led-reference"}


def test_pro_query_excludes_explicit_home_documents(retriever):
    hits = retriever.retrieve("N5 Pro node flashing amber should I move it", product_line="pro")
    assert hits
    assert _sources(hits)[0] == "pro-led-reference"
    assert "troubleshooting-guide" not in _sources(hits)   # explicit "Applies to: R1 and N1"


def test_unknown_product_returns_both_lines_so_the_agent_can_ask(retriever):
    hits = retriever.retrieve("node flashing amber", product_line=None)
    lines = {h.chunk.product_line for h in hits}
    assert lines >= {"home", "pro"}


def test_archived_workaround_ranks_below_current_release_notes(retriever):
    hits = retriever.retrieve("clients drop between nodes disable band steering roaming", product_line="home")
    src = _sources(hits)
    assert "firmware-release-notes" in src
    if "firmware-archive" in src:
        assert src.index("firmware-release-notes") < src.index("firmware-archive")
    assert all(h.chunk.archived is False for h in hits[:1])


def test_error_code_retrieves_error_table_and_procedure(retriever):
    hits = retriever.retrieve("E17 router already configured during setup", product_line="home")
    assert {"led-reference", "troubleshooting-guide", "reset-recovery-guide"} & set(_sources(hits))


def test_state_enriched_query_recovers_context(retriever):
    # A terse follow-up alone is ambiguous; the agent appends known facts to the query.
    hits = retriever.retrieve("it is flashing amber now N1 wireless", product_line="home")
    assert any(h.chunk.locator == "N1 node LEDs" or "disconnects" in h.chunk.locator for h in hits)


def test_find_is_a_deterministic_lookup(retriever):
    chunks = retriever.find("reset-recovery-guide", "Factory reset")
    assert len(chunks) == 1 and "erases" in chunks[0].text


def test_weak_evidence_is_detected_for_off_topic_questions(retriever):
    from orbitmesh.retrieval import evidence_is_weak
    q = "How do I set up port forwarding for my PlayStation?"
    assert evidence_is_weak(q, retriever.retrieve(q))
    q = "N1 flashing amber on wireless backhaul"
    assert not evidence_is_weak(q, retriever.retrieve(q, product_line="home"))
    assert not evidence_is_weak("E31", retriever.retrieve("E31", product_line="home"))


def test_a_pro_customer_asking_about_warranty_gets_the_warranty_policy(retriever):
    hits = retriever.retrieve("Will the warranty definitely cover a replacement if I send it in?", product_line="pro")
    assert ("warranty-safety-policy", "Limited warranty") in [(h.chunk.source_id, h.chunk.locator) for h in hits[:3]]


def test_find_prefers_the_exact_section_over_a_title_that_contains_the_word(retriever):
    found = retriever.find("warranty-safety-policy", "Safety")
    assert found and found[0].locator == "Safety"        # not "OrbitMesh Warranty, Safety, and Escalation Policy"
