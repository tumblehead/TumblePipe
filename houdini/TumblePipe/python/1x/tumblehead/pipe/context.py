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

def get_aov_names_from_context(context_data: dict, variant: str = None) -> list[str]:
    """Extract AOV names from context.json outputs.

    Args:
        context_data: The loaded context.json dictionary
        variant: Optional variant name to filter outputs

    Returns:
        List of AOV names, or empty list if not found
    """
    if context_data is None:
        return []

    outputs = context_data.get('outputs', [])
    for output in outputs:
        if variant is not None and output.get('variant') != variant:
            continue
        params = output.get('parameters', {})
        aov_names = params.get('aov_names', [])
        if aov_names:
            return aov_names

    return []
