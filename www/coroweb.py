# -*- coding: utf-8 -*-

'''
web frame 框架模块
'''

import asyncio, os, inspect, logging, functools, time

from datetime import datetime
from urllib import parse
from aiohttp import web
from apis import APIError
from jinja2 import Environment, FileSystemLoader

'''
将aiohttp框架进一步封装成更简明使用的web框架
建立视图函数装饰器，用来存储、附带URL信息，这样子便可以直接通过装饰器，将函数映射成视图函数
例：@get
	def View(request):
		return response
	但此时函数View仍未能从request请求中提取相关的参数，
	需自行定义一个处理request请求的类来封装，并把视图函数变为协程
'''

def get(path):
	' @get装饰器，给处理函数绑定URL和HTTP method-GET的属性 '
	def decorator(func):
    	#functools.wraps在装饰器中方便拷贝被装饰函数的签名(使包装的函数有__name__,__doc__属性)
		@functools.wraps(func)
		def wrapper(*args, **kw):
			return func(*args, **kw)
		wrapper.__method__ = 'GET'
		wrapper.__route__ = path
		return wrapper
	return decorator


def post(path):
	' @post装饰器，给处理函数绑定URL和HTTP method-POST的属性 '
	def decorator(func):
		@functools.wraps(func)
		def wrapper(*args, **kw):
			return func(*args, **kw)
		wrapper.__method__ = 'POST'
		wrapper.__route__ = path
		return wrapper
	return decorator

#----------------inspect模块，检查视图函数的参数，使用 RequestHandler 同一组合成dict形式，传入函数------------------
'''
VAR_POSITIONAL：对应 *args的参数，
KEYWORD_ONLY：对应命名关键字参数，即*，*args之后的参数
VAR_KEYWORD：对应 **args的参数
param.default：获取参数默认值，如果没有默认值，将被设置成Parameter.empty
param.kind：描述参数值的属性
'''

def has_request_arg(fn):
	' 检查函数是否有request参数，返回布尔值。，否则抛出异常 '
	params = inspect.signature(fn).parameters # 含有 参数名，参数 的信息
	found = False
	for name, param in params.items():
		if name == 'request':
			found = True
			continue
		'''
		若有request参数，则：
		1、该参数必须为可变参数、命名关键字参数、关键字参数之前的最后一个参数（后面不能出现位置参数）
		2、为最后一个参数
		'''
		if found and (param.kind != inspect.Parameter.VAR_POSITIONAL and param.kind != inspect.Parameter.KEYWORD_ONLY and param.kind != inspect.Parameter.VAR_KEYWORD):
			raise ValueError('request parameter must be the last named parameter in function: %s%s' % (fn.__name__, str(sig)))
	return found

#收集没有默认值的命名关键字参数 必要参数
def get_required_kw_args(fn):
	' 将函数所有 没默认值的 命名关键字参数名 作为一个tuple返回 '
	args = []
	params = inspect.signature(fn).parameters
	for name, param in params.items():
		if param.kind == inspect.Parameter.KEYWORD_ONLY and param.default == inspect.Parameter.empty:
			args.append(name)
	return tuple(args)

#判断有没有关键字参数
def has_var_kw_arg(fn):
	' 检查函数是否有关键字参数集，返回布尔值 '
	params = inspect.signature(fn).parameters
	for name, param in params.items():
		if param.kind == inspect.Parameter.VAR_KEYWORD:
			return True

#获取命名关键字参数
def has_named_kw_args(fn):
	' 检查函数是否有命名关键字参数，返回布尔值 '
	params = inspect.signature(fn).parameters
	for name, param in params.items():
		if param.kind == inspect.Parameter.KEYWORD_ONLY:
			return True

#获取命名关键字参数
def get_named_kw_args(fn):
	' 将函数所有的 命名关键字参数名 作为一个tuple返回 '
	args = []
	params = inspect.signature(fn).parameters
	for name, param in params.items():
		if param.kind == inspect.Parameter.KEYWORD_ONLY:
			args.append(name)
	return tuple(args)

'''
URL处理函数不一定是一个coroutine，因此我们用RequestHandler()来封装一个URL处理函数。
RequestHandler是一个类，由于定义了__call__()方法，因此可以将其实例视为函数。
RequestHandler目的就是从URL函数中分析其需要接收的参数，
从request中获取必要的参数，调用URL函数，然后把结果转换为web.Response对象，这样，就完全符合aiohttp框架的要求：

request是经aiohttp包装后的对象，其本质是一个HTTP请求，由请求状态(status)，请求首部(header)，内容实体(body)组成
我们需要的参数包含在 内容实体 和 请求状态的URL 中
'''

class RequestHandler(object):
	' 请求处理器，用来封装处理函数 '
	def __init__(self, app, fn):
		# app : an application instance for registering the fn
		# fn : a request handler with a particular HTTP method and path
		self._app = app
		self._func = fn
		self._has_request_arg = has_request_arg(fn) # 检查函数是否有request参数
		self._has_var_kw_arg = has_var_kw_arg(fn) # 检查函数是否有关键字参数集
		self._has_named_kw_args = has_named_kw_args(fn) # 检查函数是否有命名关键字参数
		self._named_kw_args = get_named_kw_args(fn) # 将函数所有的 命名关键字参数名 作为一个tuple返回
		self._required_kw_args = get_required_kw_args(fn) # 将函数所有 没默认值的 命名关键字参数名 作为一个tuple返回

	#定义__call__()方法可以将类的实例视为函数
	#分析请求 将处理函数需要的参数解析成字典 传入处理函数
	async def __call__(self, request):
		kw = None
		if self._has_var_kw_arg or self._has_named_kw_args or self._required_kw_args:
			# 当传入的处理函数具有 关键字参数集 或 命名关键字参数 或 request参数
			if request.method == 'POST':
				# POST请求预处理
				if not request.content_type:
					# 无正文类型信息时返回
					return web.HTTPBadRequest('Missing Content-Type.')
				ct = request.content_type.lower()
				if ct.startswith('application/json'):
					# 处理JSON类型的数据，传入参数字典中
					params = await request.json()
					if not isinstance(params, dict):
						return web.HTTPBadRequest('JSON body must be object.')
					kw = params
				elif ct.startswith('application/x-www-form-urlencoded') or ct.startswith('multipart/form-data'):
					# 处理表单类型的数据，传入参数字典中，form表单请求的编码形式
					params = await request.post()
					kw = dict(**params) # 直接读取post的信息，dict-like对象
				else:
					# 暂不支持处理其他正文类型的数据
					return web.HTTPBadRequest('Unsupported Content-Type: %s' % request.content_type)
			if request.method == 'GET':
				# GET请求预处理 #以字符串的形式返回url查询语句 ? 后的键值
				qs = request.query_string
				# 获取URL中的请求参数，如 name=Justone, id=007
				if qs:
					# 将请求参数传入参数字典中
					kw = dict()
					#解析作为字符串参数给出的查询字符串，返回字典
					for k, v in parse.parse_qs(qs, True).items():
						kw[k] = v[0]
		if kw is None:
			'''若request中无参数
				request.match_info返回dict对象，可变路由中的可变字段{variable}为参数名，传入的request请求path为值
				例子：可变路由：/a/{name}/c，可匹配的path为：/a/jack/c的request(请求)
				则request.match_info返回{name=jack}
			'''
			kw = dict(**request.match_info)
		else:
			# 参数字典收集请求参数
			if not self._has_var_kw_arg and self._named_kw_args:
    			#当视图函数没有关键字参数时，移除request中不在命名关键字参数中的参数:
				copy = dict()
				for name in self._named_kw_args:
					if name in kw:
						copy[name] = kw[name]
				kw = copy
			#判断url路径中是否有参数和request中内容实体的参数相同,url路径也要作为参数存入kw中
			for k, v in request.match_info.items():
				if k in kw:
					logging.warning('Duplicate arg name in named arg and kw args: %s' % k)
				kw[k] = v
		#request实例在构造url处理函数中必不可少
		if self._has_request_arg:
			kw['request'] = request
		#没有默认值的命名关键字参数不存在 抛出异常
		if self._required_kw_args:
			# 收集无默认值的关键字参数
			for name in self._required_kw_args:
				if not name in kw:
					# 当存在关键字参数未被赋值时返回，例如 一般的账号注册时，没填入密码就提交注册申请时，提示密码未输入
					return web.HTTPBadRequest('Missing arguments: %s' % name)
		logging.info('call with args: %s' % str(kw))
		try:
			r = await self._func(**kw)
			# 最后调用处理函数，并传入请求参数，进行请求处理
			return r
		except APIError as e:
			return dict(error=e.error, data=e.data, message=e.message)

#添加静态文件，如image,css,javascript等
def add_static(app):
	' 添加静态资源路径 '
	#__file__返回当前模块的路径(如果sys.path包含当前模块则返回相对路径，否则绝对路径)
	path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static') #获得包含'static'的绝对路径
	# os.path.dirname(os.path.abspath(__file__)) 返回脚本所在目录的绝对路径
	app.router.add_static('/static/', path) # 添加静态资源路径
	logging.info('add static %s => %s' % ('/static/', path))


def add_route(app, fn):
	' 将处理函数注册到web服务程序的路由当中 '
	method = getattr(fn, '__method__', None) # 获取 fn 的 __method__ 属性的值，无则为None
	path = getattr(fn, '__route__', None) # 获取 fn 的 __route__ 属性的值，无则为None
	if path is None or method is None:
		raise ValueError('@get or @post not define in %s.' % str(fn))
	if not asyncio.iscoroutinefunction(fn) and not inspect.isgeneratorfunction(fn):
		# 当处理函数不是协程时，封装为协程函数
		fn = asyncio.coroutine(fn)
	logging.info('add route %s %s => %s(%s)' % (method, path, fn.__name__, ', '.join(inspect.signature(fn).parameters.keys())))
	app.router.add_route(method, path, RequestHandler(app, fn))

#批量注册视图函数
def add_routes(app, module_name):
	' 自动把handler模块符合条件的函数注册 '
	n = module_name.rfind('.')
	if n == (-1):
		# 没有匹配项时。普通模块的使用
		mod = __import__(module_name, globals(), locals())
		# import一个模块，获取模块名 __name__
	else:
		# 添加模块属性 name，并赋值给mod。包中模块的使用.mod.name形式
		name = module_name[n+1:]
		mod = getattr(__import__(module_name[:n], globals(), locals(), [name]), name)
	for attr in dir(mod):
		# dir(mod) 获取模块所有属性
		if attr.startswith('_'):
			# 略过所有私有属性
			continue
		fn = getattr(mod, attr)
		# 获取属性的值，可以是一个method
		if callable(fn):
			method = getattr(fn, '__method__', None)
			path = getattr(fn, '__route__', None)
			if method and path:
				# 对已经修饰过的URL处理函数注册到web服务的路由中
				add_route(app, fn)

def init_jinja2(app, **kw):
    logging.info('init jinja2...')
    # 设置前段模版字符串
    options = dict(
        #自动转义xml/html的特殊字符
        autoescape = kw.get('autoescape', True),
        #代码块的开始、结束标志
        block_start_string = kw.get('block_start_string', '{%'),
        block_end_string = kw.get('block_end_string', '%}'),
        #变量的开始、结束标志
        variable_start_string = kw.get('variable_start_string', '{{'),
        variable_end_string = kw.get('variable_end_string', '}}'),
        auto_reload = kw.get('auto_reload', True)
    )
    #获取模板文件夹路径
    path = kw.get('path', None)
    if path is None:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'templates')
    logging.info('set jinja2 template path: %s' % path)
    #Environment类是jinja2的核心类，用来保存配置、全局对象以及模板文件的路径
	#FileSystemLoader类加载Path路径中的模板文件
    env = Environment(loader=FileSystemLoader(path), **options)
    #过滤器集合
    filters = kw.get('filters', None)
    if filters is not None:
        for name, f in filters.items(): #filters是Enviroment类的属性：过滤器字典
            env.filters[name] = f
    app['__templating__'] = env #app是一个dict-like对象

# 用于 jinjia2 前端显示
def datetime_filter(t):
    delta = int(time.time() - t)
    if delta < 60:
        return u'1分钟前'
    if delta < 3600:
        return u'%s分钟前' % (delta // 60)
    if delta < 86400:
        return u'%s小时前' % (delta // 3600)
    if delta < 604800:
        return u'%s天前' % (delta // 86400)
    dt = datetime.fromtimestamp(t)
    return u'%s年%s月%s日' % (dt.year, dt.month, dt.day)