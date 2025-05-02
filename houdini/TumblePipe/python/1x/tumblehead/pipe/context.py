def find_input(data, **kwargs):
    for context_input in data['inputs']:
        if 'context' in kwargs and context_input['context'] != kwargs['context']: continue
        if 'stage' in kwargs and context_input['stage'] != kwargs['stage']: continue
        if 'asset' in kwargs and context_input['asset'] != kwargs['asset']: continue
        if 'sequence' in kwargs and context_input['sequence'] != kwargs['sequence']: continue
        if 'shot' in kwargs and context_input['shot'] != kwargs['shot']: continue
        if 'version' in kwargs and context_input['version'] != kwargs['version']: continue
        return context_input

def find_output(data, **kwargs):
    for context_output in data['outputs']:
        if 'context' in kwargs and context_output['context'] != kwargs['context']: continue
        if 'stage' in kwargs and context_output['stage'] != kwargs['stage']: continue
        if 'asset' in kwargs and context_output['asset'] != kwargs['asset']: continue
        if 'sequence' in kwargs and context_output['sequence'] != kwargs['sequence']: continue
        if 'shot' in kwargs and context_output['shot'] != kwargs['shot']: continue
        if 'version' in kwargs and context_output['version'] != kwargs['version']: continue
        return context_output

def list_inputs(data, **kwargs):
    result = []
    for context_input in data['inputs']:
        if 'context' in kwargs and context_input['context'] != kwargs['context']: continue
        if 'stage' in kwargs and context_input['stage'] != kwargs['stage']: continue
        if 'asset' in kwargs and context_input['asset'] != kwargs['asset']: continue
        if 'sequence' in kwargs and context_input['sequence'] != kwargs['sequence']: continue
        if 'shot' in kwargs and context_input['shot'] != kwargs['shot']: continue
        if 'version' in kwargs and context_input['version'] != kwargs['version']: continue
        result.append(context_input)
    return result

def list_outputs(data, **kwargs):
    result = []
    for context_output in data['outputs']:
        if 'context' in kwargs and context_output['context'] != kwargs['context']: continue
        if 'stage' in kwargs and context_output['stage'] != kwargs['stage']: continue
        if 'asset' in kwargs and context_output['asset'] != kwargs['asset']: continue
        if 'sequence' in kwargs and context_output['sequence'] != kwargs['sequence']: continue
        if 'shot' in kwargs and context_output['shot'] != kwargs['shot']: continue
        if 'version' in kwargs and context_output['version'] != kwargs['version']: continue
        result.append(context_output)
    return result