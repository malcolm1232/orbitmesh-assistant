from orbitmesh.conversation import SessionState, SessionStore


def test_facts_are_extracted_from_customer_text():
    s = SessionState("t")
    s.observe_customer("It's an N1 on wireless, the light is flashing amber and the app says E24, firmware 3.4.1")
    assert s.facts["product_line"] == "home"
    assert s.facts["device"] == "N1"
    assert s.facts["led"] == "flashing amber"
    assert s.facts["error_code"] == "E24"
    assert s.facts["firmware"] == "3.4.1"
    assert s.facts["backhaul"] == "wireless"


def test_pro_hardware_and_no_light_and_safety():
    s = SessionState("t")
    s.observe_customer("My N5 Pro has no light at all and the adapter smells burnt")
    assert s.facts["product_line"] == "pro" and s.facts["device"] == "N5 Pro"
    assert s.facts["led"] == "no light"
    assert s.safety_condition


def test_led_variants_normalise():
    s = SessionState("t")
    s.observe_customer("the LED is blinking orange")
    assert s.facts["led"] == "flashing amber"
    s.observe_customer("now it's steady white")
    assert s.facts["led"] == "solid white"


def test_tried_steps_and_resolution_are_remembered():
    s = SessionState("t")
    s.observe_customer("I already restarted it twice")
    assert s.steps_tried
    s.observe_customer("ok that fixed it")
    assert s.facts.get("customer_reports_resolved") is True


def test_confirmation_parsing():
    s = SessionState("t")
    assert s.customer_confirms("yes go ahead") is True
    assert s.customer_confirms("No, not yet") is False
    assert s.customer_confirms("what will I lose?") is None


def test_model_facts_never_override_extracted_ones():
    s = SessionState("t")
    found = s.observe_customer("N1 flashing amber")
    s.merge_model_facts({"led": "solid white", "symptom": "drops hourly", "tried": ["restart"]}, protected=found)
    assert s.facts["led"] == "flashing amber"
    assert s.facts["symptom"] == "drops hourly"
    assert "restart" in s.steps_tried


def test_sessions_persist_across_store_instances(tmp_path):
    store = SessionStore(tmp_path)
    s = store.get("case-1")
    s.observe_customer("R1 shows E17")
    s.remember("customer", "R1 shows E17")
    store.save(s)
    again = SessionStore(tmp_path).get("case-1")
    assert again.facts["error_code"] == "E17" and again.history[0]["content"] == "R1 shows E17"
    store.reset("case-1")
    assert SessionStore(tmp_path).get("case-1").facts == {}
