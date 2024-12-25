#Experimental eager cache eviction
#Expect things to break if any dynamic prompts are used
import functools
from comfy_execution.caching import HierarchicalCache, CacheKeySetInputSignature, CacheKeySetID
from comfy_execution import graph
import execution

def is_link(inp):
    return isinstance(inp, list) and len(inp) == 2
def link_count(dynprompt, node_id):
    return sum([is_link(x) for x in dynprompt.get_node(node_id)['inputs'].values()])

class MinCache(HierarchicalCache):
    def set_prompt(self, dynprompt, node_ids, is_changed_cache):
        super().set_prompt(dynprompt, node_ids, is_changed_cache)
        self.dependents = {}
        for node_id in node_ids:
            inputs = dynprompt.get_node(node_id)['inputs']
            for inp in inputs.values():
                if isinstance(inp, list) and len(inp) == 2:
                    if inp[0] not in self.dependents:
                        self.dependents[inp[0]] = []
                    self.dependents[inp[0]].append(node_id)
    def set(self, node_id, value):
        super().set(node_id, value)
        inputs = self.dynprompt.get_node(node_id)['inputs']
        for inp in inputs.values():
            if not is_link(inp):
                continue
            input_id = inp[0]
            self.dependents[input_id].remove(node_id)
            if len(self.dependents[input_id]) == 0:
                cache_key = self.cache_key_set.get_data_key(input_id)
                del self.cache[cache_key]

def init_cache(self):
    self.outputs = MinCache(CacheKeySetInputSignature)
    self.ui = HierarchicalCache(CacheKeySetInputSignature)
    self.objects = HierarchicalCache(CacheKeySetID)
execution.CacheSet.init_classic_cache = init_cache

class MincacheExecutionList(graph.ExecutionList):
    def __init__(self, *args, **kwargs):
        print('init')
        super().__init__(*args, **kwargs)
        self.depth = {}
    def stage_node_execution(self):
        assert self.staged_node_id is None
        if self.is_empty():
            return None, None, None
        available = self.get_ready_nodes()
        if len(available) == 0:
            #aint got time for this
            return super().stage_node_execution()
        available.sort(key=lambda x: (-link_count(self.dynprompt, x),
                                      -self.depth.get(x,0),
                                      len(self.blocking[x]), x))
        print([self.dynprompt.get_node(x)['class_type'] for x in available])
        self.staged_node_id = available[0]
        return self.staged_node_id, None, None
    def add_strong_link(self, from_node_id, from_socket, to_node_id):
        super().add_strong_link(from_node_id, from_socket, to_node_id)
        self.depth[from_node_id] = max(self.depth.get(to_node_id, 0) + 1,
                                       self.depth.get(from_node_id, 0))
execution.ExecutionList = MincacheExecutionList

'''
Prioritize
- A computation that allows clearing a cached result
- A computation that progresses towards clearing a cached item
- A computation that is of the greatest depth for cached items
 - depth is 1+max(0, *dependent_depths)

sort nodes by tuple (-num_cached_dependencies, uncached_dependencies (always 0?), -depth)
'''
