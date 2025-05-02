WORD_ALPHABET = set('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.*')

class StorageConvention:

    def is_valid_path(self, path):
        if ':' not in path: return False
        purpose, resource = path.split(':', 1)
        if not set(purpose).issubset(WORD_ALPHABET): return False
        if ':' in resource: return False
        if not resource.startswith('/'): return False
        for part in filter(lambda part: len(part) > 0, resource.split('/')[1:]):
            if not set(part).issubset(WORD_ALPHABET): return False
        return True
    
    def parse_path(self, path):
        purpose, path = path.split(':', 1)
        return purpose, path.split('/')[1:]

    def resolve(self, path):
        raise NotImplementedError()