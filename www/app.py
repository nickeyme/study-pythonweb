# -*- coding: utf-8 -*-

import logging

import asyncio, os, json, time
from datetime import datetime
from aiohttp import web
from config import configs
from aiohttp.web import middleware

'''
def index(request):
    #return web.Response(body=b'<h1>Awesome</h1>') # 不加返回的内容类型 默认为下载文件
    return web.Response(body=b'<h1>Awesome</h1>',content_type='text/html')

# localhost:8080
async def init(loop):
    app = web.Application(loop=loop)
    app.router.add_route('GET', '/', index)
    srv = await loop.create_server(app.make_handler(), '127.0.0.1', 8080) # yield from
    logging.info('server started at http://127.0.0.1:8080...')
    return srv
'''
#日志文件简单配置basicConfig(filename/stream,filemode='a',format,datefmt,level)
logging.basicConfig(level=logging.INFO)


import orm
from coroweb import add_routes, add_static, init_jinja2, datetime_filter
from handlers import cookie2user, COOKIE_NAME

#aiohttp V2.3以后的新式写法，参数handler是视图函数 @middleware

# 过滤去 打印日志
async def logger_factory(app, handler):
    async def logger(request):
        logging.info('Request: %s %s' % (request.method, request.path))
        # await asyncio.sleep(0.3)
        return (await handler(request))
    return logger

'''
async def data_factory(app, handler):
    async def parse_data(request):
        if request.method == 'POST':
            if request.content_type.startswith('application/json'):
                request.__data__ = await request.json()
                logging.info('request json: %s' % str(request.__data__))
            elif request.content_type.startswith('application/x-www-form-urlencoded'):
                request.__data__ = await request.post()
                logging.info('request form: %s' % str(request.__data__))
        return (await handler(request))
    return parse_data
'''

# 把当前用户绑定到request上，并对URL/manage/进行拦截，检查当前用户是否是管理员身份
# 不是管理员用户则跳转 /signin 页面，否则继续处理当前请求
async def auth_factory(app, handler):
    async def auth(request):
        logging.info('check user: %s %s' % (request.method, request.path))
        request.__user__ = None
        logging.info(request.cookies)
        cookie_str = request.cookies.get(COOKIE_NAME) # 获取名为COOKIE_NAME的cookie字符串
        if cookie_str:
            user = await cookie2user(cookie_str) # 验证并转换cookie
            if user:
                logging.info('set current user: %s' % user.email)
                request.__user__ = user
        # 此处判定/manage的子url中的请求是否有权限或者当前登录是否超时，否则返回signin登陆界面
        if request.path.startswith('/manage/') and (request.__user__ is None or not request.__user__.admin):
            return web.HTTPFound('/signin')
        return (await handler(request))
    return auth

# 处理所有请求后 将结果包装成 web.Response 对象进行返回
# 最终处理请求，返回响应给客户端
async def response_factory(app, handler):
    async def response(request):
        logging.info('Response handler...')
        r = await handler(request)
        # 如果经过句柄函数（视图函数）handler处理后的请求是stream流响应的实例，则直接返回给客户端
        if isinstance(r, web.StreamResponse):
            return r
        # 如果处理后是字节的实例，则调用web.Response并添加头部返回给客户端
        if isinstance(r, bytes):
            resp = web.Response(body=r)
            resp.content_type = 'application/octet-stream'
            return resp
        #如果处理后是字符串的实例，则需调用web.Response并(utf-8)编码成字节流，添加头部返回给客户端
        if isinstance(r, str):
            logging.info('return str.encode(`utf-8`)')
            #如果开头的字符串是redirect:形式（重定向），则返回重定向后面字符串所指向的页面
            if r.startswith('redirect:'):
                return web.HTTPFound(r[9:])
            resp = web.Response(body=r.encode('utf-8'))
            resp.content_type = 'text/html;charset=utf-8'
            return resp
        #如果处理后是字典的实例
        if isinstance(r, dict):
            #在后续构造视图函数返回值时，会加入__template__值，用以选择渲染的模板
            template = r.get('__template__')
            if template is None:
				'''不带模板信息，返回json对象
				ensure_ascii:默认True，仅能输出ascii格式数据。故设置为False
				default: r对象会先被传入default中的函数进行处理，然后才被序列化为json对象
				__dict__: 以dict形式返回对象属性和值的映射，一般的class实例都有一个__dict__属性'''
                resp = web.Response(body=json.dumps(r, ensure_ascii=False, default=lambda o: o.__dict__).encode('utf-8'))
                resp.content_type = 'application/json;charset=utf-8'
                return resp
            else: # 模版处理返回前端页面的地方
                '''get_template()方法返回Template对象，调用其render()方法传入r渲染模板'''
                r['__user__'] = request.__user__ # 这行非常重要 登录后记录用户登录的状态
                resp = web.Response(body=app['__templating__'].get_template(template).render(**r).encode('utf-8'))
                resp.content_type = 'text/html;charset=utf-8'
                return resp
        # 直接返回响应码
        if isinstance(r, int) and r >= 100 and r < 600:
            return web.Response(r)
        # 返回响应码和message
        if isinstance(r, tuple) and len(r) == 2:
            t, m = r
            if isinstance(t, int) and t >= 100 and t < 600:
                return web.Response(t, str(m))
        # default:
        resp = web.Response(body=str(r).encode('utf-8'))
        resp.content_type = 'text/plain;charset=utf-8'
        return resp
    return response



'''
middleware（中间件）是一种拦截器，一个URL在被某个函数处理前，可以经过一系列的middleware的处理。
一个middleware可以改变URL的输入、输出，甚至可以决定不继续处理而直接返回。
middleware的用处就在于把通用的功能从每个URL处理函数中拿出来，集中放到一个地方。

middlewares 其实是一种拦截器机制，可以在处理 request 请求的前后先经过拦截器函数处理一遍，
比如可以统一打印 request 的日志等等，它的原理就是 python 的装饰器，
不知道装饰器的同学还请自行谷歌，middlewares 接收一个列表，
列表的元素就是你写的拦截器函数，for 循环里以倒序分别将 url 处理函数用拦截器装饰一遍。
最后再返回经过全部拦截器装饰过的函数。这样在你最终调用 url 处理函数之前就可以进行一些额外的处理啦。


Application,构造函数 def __init__(self,*,logger=web_logger,loop=None,
                                router=None,handler_factory=RequestHandlerFactory,
                                middlewares=(),debug=False)

使用app时，先将urls注册进router，再用aiohttp.RequestHandlerFactory作为协议簇创建套接字
'''
async def init(loop):
    await orm.create_pool(loop=loop, **configs.db) # **config.db 直接传入字典
    app = web.Application(loop=loop, middlewares=[
        logger_factory, auth_factory, response_factory
    ])
    init_jinja2(app, filters=dict(datetime=datetime_filter))
    add_routes(app, 'handlers')
    add_static(app)

    #用make_handler()创建aiohttp.RequestHandlerFactory，用来处理HTTP协议
	'''用协程创建监听服务，其中LOOP为传入函数的协程，调用其类方法创建一个监听服务，声明如下
	   coroutine BaseEventLoop.create_server(protocol_factory,host=None,port=None,*,
	                                         family=socket.AF_UNSPEC,flags=socket.AI_PASSIVE
	                                         ,sock=None,backlog=100,ssl=None,reuse_address=None
	                                         ,reuse_port=None)
	    await返回后使srv的行为模式和LOOP.create_server()一致'''

    srv = await loop.create_server(app.make_handler(), '127.0.0.1', 9000)
    logging.info('server started at http://127.0.0.1:9000...')
    return srv

#创建协程，LOOP = asyncio.get_event_loop()为asyncio.BaseEventLoop的对象，协程的基本单位
loop = asyncio.get_event_loop()
loop.run_until_complete(init(loop))
loop.run_forever()