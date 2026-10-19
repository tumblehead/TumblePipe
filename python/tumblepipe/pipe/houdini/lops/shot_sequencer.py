"""th::shot_sequencer: lays its input shots end to end on one timeline.

The HDA's internal ``set_stage_framerange`` (Configure Layer) reads the
sequence's end time from `end_time`.
"""


def sequence_end(seq_startframe, shot_lengths):
    """The sequence's end time: its start plus every shot's length.

    No shots is an empty sequence, which ends where it starts.
    """
    return seq_startframe + sum(shot_lengths)


def end_time(hda_node):
    """`sequence_end` from the HDA's Sequence Start Frame and `shots` rows."""
    return sequence_end(
        hda_node.evalParm('seq_startframe'),
        [
            hda_node.evalParm(f'shotlength{index}')
            for index in range(1, hda_node.evalParm('shots') + 1)
        ],
    )
