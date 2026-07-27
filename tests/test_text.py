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


def test_dictionary_casing_survives_sentence_start():
    settings = Settings(
        dictionary=[Replacement("i phone", "iPhone"), Replacement("mac os", "macOS")],
    )
    assert process_text("i phone is broken", settings).text == "iPhone is broken"
    assert (
        process_text("i like mac os period mac os is fine", settings).text
        == "I like macOS. macOS is fine"
    )


def test_snippet_casing_survives_sentence_start():
    settings = Settings(snippets=[Snippet("my website", "eBay listings")])
    assert process_text("my website are up", settings).text == "eBay listings are up"


def test_ordinary_sentences_are_still_capitalized():
    settings = Settings()
    assert (
        process_text("hello there period how are you question mark", settings).text
        == "Hello there. How are you?"
    )


def test_punctuation_commands_work_as_first_word():
    settings = Settings()

    assert process_text("new paragraph today's agenda", settings).text == "Today's agenda"
    assert process_text("new line today's agenda", settings).text == "Today's agenda"
    assert process_text("question mark really", settings).text == "? Really"
    assert process_text("exclamation mark really", settings).text == "! Really"
    assert process_text("exclamation point really", settings).text == "! Really"
    assert process_text("comma really", settings).text == ", really"
    assert process_text("colon really", settings).text == ": really"
    assert process_text("semicolon really", settings).text == "; really"
    assert process_text("period really", settings).text == ". Really"


def test_punctuation_commands_ignore_substrings_inside_words():
    settings = Settings()

    assert process_text("renewed paragraph draft", settings).text == "Renewed paragraph draft"
    assert process_text("the comment about periodic sales", settings).text == (
        "The comment about periodic sales"
    )
