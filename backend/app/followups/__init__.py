"""Follow-ups module — pipeline kanban aggregator.

Reads only. Column membership is derived from existing data
(``sent_log``, ``call_outcomes``, ``mail_messages``, ``no_reply_tracker``);
notes + next_touch are the only mutable surface, stored in
``followup_notes`` (one row per lead).
"""
