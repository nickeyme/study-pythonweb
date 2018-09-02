# -*- coding: utf-8 -*-

'''
Configuration
'''

import config_default

class Dict(dict):
    '''
    Simple dict but support access as x.y style.
    一般的字典类，但是支持 x.y 的格式

    example:
    D = Dict(a='1', b='2', c='3')
    print(D.a, D.b, D.c)

    >>1 2 3
    '''
    def __init__(self, names=(), values=(), **kw):
        super(Dict, self).__init__(**kw)
        for k, v in zip(names, values):
            self[k] = v

    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError:
            raise AttributeError(r"'Dict' object has no attribute '%s'" % key)

    def __setattr__(self, key, value):
        self[key] = value

def merge(defaults, override):
    r = {}
    for k, v in defaults.items():
        if k in override:
            if isinstance(v, dict):
                r[k] = merge(v, override[k])
            else:
                r[k] = override[k]
        else:
            r[k] = v
    return r

def toDict(d):
    D = Dict()
    for k, v in d.items():
        D[k] = toDict(v) if isinstance(v, dict) else v
    return D

configs = config_default.configs

try:
    import config_override # 如果有的话 则导入merge
    configs = merge(configs, config_override.configs)
except ImportError:
    pass

# toDict前后的数据相同 但是调用方式不一样
# toDict类化后 可以直接用x.y的形式来进行数据的访问 configs.session.secret
#print(configs)
configs = toDict(configs)
#print(configs)
