from tuxflow import APP_ID
from tuxflow.tray import _StatusNotifierItem, presentation_for_state


def test_tray_presentations_cover_daemon_states():
    idle = presentation_for_state("idle")
    recording = presentation_for_state("recording")
    processing = presentation_for_state("processing")
    error = presentation_for_state("error", "Microphone unavailable")

    assert idle.icon_name == APP_ID
    assert idle.status == "Active"
    assert "Ready" in idle.title
    assert recording.icon_name == "media-record"
    assert recording.status == "NeedsAttention"
    assert "Recording" in recording.title
    assert processing.icon_name == "view-refresh"
    assert "Transcribing" in processing.title
    assert error.icon_name == "dialog-error"
    assert error.description == "Microphone unavailable"


def test_status_notifier_item_updates_properties():
    item = _StatusNotifierItem()

    assert item.Title == "TuxFlow — Ready"
    item.set_state("recording")
    assert item.Title == "TuxFlow — Recording"
    assert item.IconName == "media-record"
    assert item.Status == "NeedsAttention"
    assert item.ToolTip[3] == "Listening… release the shortcut to transcribe"

    item.set_state("processing")
    assert item.Title == "TuxFlow — Transcribing"
    assert item.IconName == "view-refresh"
