"""Language detection, localised frame text, and language-independent safety."""

import pytest

from src.agents.supervisor import supervisor_agent
from src.core import i18n
from src.datascience.language import language_detector


@pytest.mark.parametrize("text,lang", [
    ("¿Puedo ver mi saldo y mi factura por favor?", "es"),
    ("मेरा बैलेंस कितना है", "hi"),
    ("What is my balance please", "en"),
])
def test_detects_supported_languages(text, lang):
    assert language_detector.detect(text).language == lang


def test_short_messages_keep_the_conversation_language():
    assert language_detector.detect("ok", hint="es").language == "es"
    assert language_detector.detect("1234", hint="hi").confidence == 0.0


def test_every_catalogue_key_has_all_supported_languages():
    cov = i18n.coverage()
    assert cov["en"] == cov["es"] == cov["hi"] == 1.0


def test_confirmation_prompt_is_localised_and_safe_in_hindi():
    supervisor_agent.process_turn("मेरा बैलेंस कितना है", "SUB-556201", session_id="hi-1")
    r = supervisor_agent.process_turn("रिचार्ज करना है $10 का", "SUB-556201", session_id="hi-1")
    assert r.language == "hi" and r.pending_action
    assert "हाँ" in r.final_response or "YES" in r.final_response
    done = supervisor_agent.process_turn("हाँ", "SUB-556201", session_id="hi-1")      # a Hindi YES confirms the action
    assert "recharge" in done.final_response.lower() or "PAY-" in done.final_response


def test_hindi_no_cancels():
    supervisor_agent.process_turn("रिचार्ज करना है $10 का", "SUB-556201", session_id="hi-2")
    r = supervisor_agent.process_turn("नहीं", "SUB-556201", session_id="hi-2")
    assert "cancel" in r.final_response.lower() or "रद्द" in r.final_response


def test_spanish_human_request_hands_over():
    r = supervisor_agent.process_turn("Quiero hablar con un agente", "SUB-102948", session_id="es-1")
    assert r.escalated_to_human
