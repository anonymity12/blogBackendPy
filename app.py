# -*- coding: utf-8 -*-
# @Author: JeffLee

import datetime
import functools
import os
import re
import urllib

from flask import (Flask, abort, flash, redirect, render_template,
                   request, Response, session, url_for)
from markupsafe import Markup
from markdown import markdown
from markdown.extensions.codehilite import CodeHiliteExtension
from markdown.extensions.extra import ExtraExtension
from micawber import bootstrap_basic, parse_html
from micawber.cache import Cache as OEmbedCache
from peewee import *
from playhouse.flask_utils import FlaskDB, get_object_or_404, object_list
from playhouse.sqlite_ext import *

ADMIN_PASSWORD = 'strong2025'
APP_DIR = os.path.dirname(os.path.realpath(__file__))
DATABASE = 'sqliteext:///%s' % os.path.join(APP_DIR, 'blog.db')
DEBUG = False
SECRET_KEY = 'shhh, secret!'  # Flask App 中加密会话 cookie 的密钥
SITE_WIDTH = 800

app = Flask(__name__)
app.config.from_object(__name__)

flask_db = FlaskDB(app)
database = flask_db.database

oembed_providers = bootstrap_basic(OEmbedCache())


class Entry(flask_db.Model):
    title = CharField()
    slug = CharField(unique=True)
    content = TextField()
    published = BooleanField(index=True)
    timestamp = DateTimeField(default=datetime.datetime.now, index=True)

    @property
    def html_content(self):
        hilite = CodeHiliteExtension(linenums=False, css_class='highlight')
        extras = ExtraExtension()
        markdown_content = markdown(self.content, extensions=[hilite, extras])
        oembed_content = parse_html(
            markdown_content,
            oembed_providers,
            urlize_all=True,
            maxwidth=app.config['SITE_WIDTH'])
        return Markup(oembed_content)

    @classmethod
    def public(cls):
        return Entry.select().where(Entry.published == True)

    @classmethod
    def search(cls, query):
        words = [word.strip() for word in query.split() if word.strip()]
        if not words:
            # 返回空查询
            return Entry.select().where(Entry.id == 0)
        else:
            search = ' '.join(words)
        return (FTSEntry
                .select(
            FTSEntry,
            Entry,
            FTSEntry.rank().alias('score'))
                .join(Entry, on=(FTSEntry.entry_id == Entry.id).alias('entry'))
                .where(
            (Entry.published == True) &
            (FTSEntry.match(search)))
                .order_by(SQL('score').desc()))

    @classmethod
    def drafts(cls):
        """ 草稿 """
        return Entry.select().where(Entry.published == False)

    def save(self, *args, **kwargs):  # 实现保存方法，更新内容时触发
        if not self.slug:
            self.slug = re.sub('[^\w]', '-', self.title.lower())
        ret = super(Entry, self).save(*args, **kwargs)

        # 存储搜索内容
        self.update_search_index()
        return ret

    def update_search_index(self):
        try:
            fts_entry = FTSEntry.get(FTSEntry.entry_id == self.id)
        except FTSEntry.DoesNotExist:
            fts_entry = FTSEntry(entry_id=self.id)
            force_insert = True
        else:
            force_insert = False
        fts_entry.content = '\n'.join((self.title, self.content))
        fts_entry.save(force_insert=force_insert)


class FTSEntry(FTSModel):  # 创建或更新搜索索引
    entry_id = IntegerField()
    content = TextField()

    class Meta:
        database = database


def login_required(fn):  # admin 登录验证的装饰器
    @functools.wraps(fn)  # 保留原函数的属性
    def inner(*args, **kwargs):
        if session.get('logged_in'):
            return fn(*args, **kwargs)
        return redirect(url_for('login', next=request.path))

    return inner


@app.route('/login/', methods=['GET', 'POST'])
def login():
    """ 登录 """
    next_url = request.args.get('next') or request.form.get('next')
    if request.method == 'POST' and request.form.get('password'):
        password = request.form.get('password')
        if password == app.config['ADMIN_PASSWORD']:
            session['logged_in'] = True
            session.permanent = True  # 使用cookie来存储session,默认过期时间一个月
            flash('登录成功！', 'success')
            return redirect(next_url or url_for('index'))
        else:
            flash('密码错误！', 'danger')
    return render_template('login.html', next_url=next_url)


@app.route('/logout/', methods=['GET', 'POST'])
def logout():
    """ 登出 """
    if request.method == 'POST':
        session.clear()
        return redirect(url_for('login'))
    return render_template('logout.html')


@app.route('/')
def index():
    search_query = request.args.get('q')
    if search_query:
        query = Entry.search(search_query)
    else:
        query = Entry.public().order_by(Entry.timestamp.desc())
    return object_list(
        'index.html',
        query,
        search=search_query,
        check_bounds=False)


@app.route('/drafts/')
@login_required
def drafts():
    """ 草稿页 """
    query = Entry.drafts().order_by(Entry.timestamp.desc())
    return object_list('index.html', query, check_bounds=False)


@app.route('/create/', methods=['GET', 'POST'])
@login_required
def create():
    """ 创建博文 """
    if request.method == 'POST':
        if request.form.get('title') and request.form.get('content'):
            entry = Entry.create(
                title=request.form['title'],
                content=request.form['content'],
                published=request.form.get('published') or False)
            flash('博文创建成功！', 'success')
            if entry.published:
                return redirect(url_for('detail', slug=entry.slug))
            else:
                return redirect(url_for('edit', slug=entry.slug))
        else:
            flash('标题和内容必填！', 'danger')
    return render_template('create.html')


@app.route('/<slug>/edit/', methods=['GET', 'POST'])
@login_required
def edit(slug):
    """ 编辑博文 """
    entry = get_object_or_404(Entry, Entry.slug == slug)
    if request.method == 'POST':
        if request.form.get('title') and request.form.get('content'):
            entry.title = request.form.get('title')
            entry.content = request.form.get('content')
            entry.published = request.form.get('published') or False
            entry.save()

            flash('博文更新成功！', 'success')
            if entry.published:
                return redirect(url_for('detail', slug=entry.slug))
            else:
                return redirect(url_for('edit', slug=entry.slug))
        else:
            flash('标题和内容必填！', 'danger')

    return render_template('edit.html', entry=entry)


@app.route('/<slug>/')
def detail(slug):
    if session.get('logged_in'):
        query = Entry.select()
    else:
        query = Entry.public()
    entry = get_object_or_404(query, Entry.slug == slug)
    return render_template('detail.html', entry=entry)


@app.template_filter('clean_querystring')
def clean_querystring(request_args, *keys_to_remove, **new_values):
    """
    过滤搜索文字中不需要的内容。
    """
    querystring = dict((key, value) for key, value in request_args.items())
    for key in keys_to_remove:
        querystring.pop(key, None)
    querystring.update(new_values)
    return urllib.urlencode(querystring)


@app.errorhandler(404)
def not_found(exc):
    return Response('<h3>Let go to Tibet or Taipei, but never this page, cause it is Not found as my room</h3>'), 404


def main():
    print("into main")
    database.create_tables([Entry, FTSEntry], safe=True)  # 初始化数据库
    print("db ok")
    app.run(host='0.0.0.0', port=10001, debug=True)


if __name__ == '__main__':
    print("tt server start")
    main()
