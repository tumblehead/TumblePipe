def find_input(data, **kwargs):
    for context_input in data['inputs']:
        # Check all provided kwargs match the input
        if all(context_input.get(key) == value for key, value in kwargs.items()):
            return context_input
    return None

def find_output(data, **kwargs):
    for context_output in data['outputs']:
        # Check all provided kwargs match the output
        if all(context_output.get(key) == value for key, value in kwargs.items()):
            return context_output
    return None

def list_inputs(data, **kwargs):
    result = []
    for context_input in data['inputs']:
        # Check all provided kwargs match the input
        if all(context_input.get(key) == value for key, value in kwargs.items()):
            result.append(context_input)
    return result

def list_outputs(data, **kwargs):
    result = []
    for context_output in data['outputs']:
        # Check all provided kwargs match the output
        if all(context_output.get(key) == value for key, value in kwargs.items()):
            result.append(context_output)
    return result
