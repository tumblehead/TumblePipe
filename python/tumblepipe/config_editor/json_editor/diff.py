from functools import partial

from .types import JsonValue, JsonRoot, _is_basic


def _value_tree(tree: JsonValue, value: JsonValue) -> JsonValue:
    # Prepare the stack and worklist
    stack = list()
    worklist = list()

    # Define the default result
    def _result(change=False, value=None):
        return dict(change=change, value=value)

    # Define the push operation
    def _push(tree, index=None):
        next_op = None if index is None else partial(_pop, index)
        if _is_basic(tree):
            stack.append(_result(value))
            return next_op
        match tree:
            case list():
                stack.append(_result(False, [None] * len(tree)))
                worklist.append(
                    list(
                        reversed(
                            [
                                partial(_push, item, index)
                                for index, item in enumerate(tree)
                            ]
                        )
                    )
                )
                return next_op
            case dict():
                stack.append(_result(False, {key: None for key in tree.keys()}))
                worklist.append(
                    list(
                        reversed(
                            [
                                partial(_push, item, key)
                                for key, item in tree.items()
                            ]
                        )
                    )
                )
                return next_op
        assert False, f"Unsupported tree type: {type(tree)}"

    # Define the pop operation
    def _pop(index):
        result = stack.pop()
        stack[-1]["change"] |= result["change"]
        stack[-1]["value"][index] = result

    # Iterate through the tree
    _push(tree)
    while len(worklist) != 0:
        values = worklist[-1]
        if len(values) == 0:
            worklist.pop()
            continue
        next_op = values.pop()()
        if next_op is not None:
            values.append(next_op)

    # Check the stack length and return the result
    assert len(stack) == 1, f"Invalid stack length: {len(stack)}"
    return stack[0]


def _diff_tree(from_value: JsonRoot, to_value: JsonRoot) -> JsonRoot:
    def _visit_list(from_list, to_list):
        # Prepare
        change = False
        result = list()

        # Check if the lists are empty
        from_length = len(from_list)
        to_length = len(to_list)
        if from_length == 0 and to_length == 0:
            return dict(change=False, value=None)

        # Find list heads
        head_length = min(from_length, to_length)
        from_head = from_list[:head_length]
        to_head = to_list[:head_length]

        # Find list tails
        from_tail = from_list[head_length:]
        to_tail = to_list[head_length:]
        tail = from_tail if len(from_tail) > len(to_tail) else to_tail

        # Visit the head of the lists
        for from_value, to_value in zip(from_head, to_head, strict=False):
            result_value = _visit(from_value, to_value)
            change |= result_value["change"]
            result.append(result_value)

        # Visit the tail of the lists
        for value in tail:
            result_value = _visit(value, None)
            change |= result_value["change"]
            result.append(result_value)

        # Done
        return dict(change=change, value=result)

    def _visit_dict(from_dict, to_dict):
        # Check if the dicts are empty
        if len(from_dict) == 0 and len(to_dict) == 0:
            return dict(change=False, value=dict())

        # Compare the two dicts
        change = False
        result = dict()

        # Check keys in from_dict
        for from_key, from_value in from_dict.items():
            if from_key not in to_dict:
                result[from_key] = _value_tree(from_value, True)
                change = True
                continue
            to_value = to_dict[from_key]
            result_value = _visit(from_value, to_value)
            change |= result_value["change"]
            result[from_key] = result_value

        # Check keys only in to_dict (new keys added)
        for to_key in to_dict:
            if to_key not in from_dict:
                result[to_key] = _value_tree(to_dict[to_key], True)
                change = True

        return dict(change=change, value=result)

    def _visit(from_value, to_value):
        if type(from_value) != type(to_value):
            return dict(change=True, value=None)
        if _is_basic(from_value):
            return dict(change=from_value != to_value, value=None)
        if isinstance(from_value, list):
            return _visit_list(from_value, to_value)
        if isinstance(from_value, dict):
            return _visit_dict(from_value, to_value)
        assert False, f"Unsupported value type: {type(from_value)}"

    return _visit_dict(from_value, to_value)
