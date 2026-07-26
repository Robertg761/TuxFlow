from tuxflow.config import Replacement, Settings, Snippet
from tuxflow.text import process_text


def test_cleanup_dictionary_snippets_and_punctuation():
    settings = Settings(
        dictionary=[Replacement("tux flow", "TuxFlow")],
        snippets=[Snippet("my website", "https://example.com")],
    )
    result = process_text(
        "um, tux flow is available at my website new line thank you exclamation point",
        settings,
    )
    assert result.text == "TuxFlow is available at https://example.com\nThank you!"
    assert result.press_enter is False


def test_press_enter_command_is_opt_in():
    enabled = Settings(press_enter_command=True)
    disabled = Settings(press_enter_command=False)

    assert process_text("hello world, press enter.", enabled).text == "Hello world"
    assert process_text("hello world, press enter.", enabled).press_enter is True
    assert process_text("hello world, press enter.", disabled).press_enter is False


def test_whole_words_only():
    settings = Settings(dictionary=[Replacement("flow", "FLOW")])
    assert process_text("workflow flow", settings).text == "Workflow FLOW"
