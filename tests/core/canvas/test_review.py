"""A reviewed remote plan uses complete, immutable reads before any write."""

from unittest.mock import Mock

import pytest

from coursewright.core.canvas.review import ReviewChangedError, ReviewedCanvasInputs


def test_validate_rejects_same_count_with_changed_targets():
    client = Mock()
    client.get_modules.return_value = [{"id": 1, "name": "Original"}]
    reviewed = ReviewedCanvasInputs(client)
    reviewed.get_modules(101)
    client.get_modules.return_value = [{"id": 2, "name": "Original"}]
    with pytest.raises(ReviewChangedError, match="changed since preview"):
        reviewed.validate()
    client.update_module.assert_not_called()


def test_recording_and_replay_copy_values_and_retain_reviewed_targets():
    modules = [{"id": 1, "name": "Original"}]
    client = Mock()
    client.get_modules.return_value = modules
    reviewed = ReviewedCanvasInputs(client)
    rendered = reviewed.get_modules(101)
    rendered[0]["id"] = 999
    reviewed.validate()
    modules[0]["id"] = 2
    assert reviewed.get_modules(101) == [{"id": 1, "name": "Original"}]
    reviewed.update_module(101, 1, {"published": True})
    client.update_module.assert_called_once_with(101, 1, {"published": True})


def test_failed_last_input_stops_before_writes():
    client = Mock()
    client.get_modules.return_value = [{"id": 1}]
    client.get_module_items.return_value = [{"id": 11}]
    reviewed = ReviewedCanvasInputs(client)
    reviewed.get_modules(101)
    reviewed.get_module_items(101, 1)
    client.get_module_items.side_effect = RuntimeError("outage")
    with pytest.raises(RuntimeError, match="outage"):
        reviewed.validate()
    client.update_module.assert_not_called()


def test_preview_cannot_send_writes():
    reviewed = ReviewedCanvasInputs(Mock())
    with pytest.raises(ReviewChangedError, match="preview attempted"):
        reviewed.update_module(101, 1, {"published": True})
