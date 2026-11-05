"""Rules about unsaved edits, kept free of widgets so they can be tested directly.

A "pane" is anything with `key` (the document identity) and `modified`.
"""
SAVE, DISCARD, CANCEL = "save", "discard", "cancel"


def locked_by_other(pane, panes):
    """A view must not edit while another view of the same PDF holds unsaved edits.

    Only one view can save a file, so the others show its last saved version.
    """
    return any(other is not pane and other.key == pane.key and other.modified for other in panes)


def collect_decisions(panes, ask):
    """Ask about every modified pane before anything closes; None as soon as the user cancels."""
    decisions = []
    for pane in panes:
        if pane.modified:
            answer = ask(pane)
            if answer == CANCEL:
                return None
            decisions.append((pane, answer))
    return decisions


def apply_decisions(decisions, save):
    """Save what was chosen to be saved; False (keep everything open) at the first failed save."""
    return all(save(pane) for pane, answer in decisions if answer == SAVE)
