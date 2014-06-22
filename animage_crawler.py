#!/usr/bin/env python

from __future__ import print_function

import sys
import requests
import bs4
import datetime
import re
import urllib
import os
import time


def print_error(*objs):
        print("[E] ", *objs, file=sys.stderr)


def print_warning(*objs):
        print("[W] ", *objs, file=sys.stderr)


class BlogPage(object):
    base_url = 'http://animage.tumblr.com'

    def __init__(self, url=None, tag=None, page=1):
        self.tag = tag
        self.page = page
        self.url = url or self.compose_url(tag, page)

    @classmethod
    def compose_url(klass, tag, page):
        if tag is None:
            return '{0}/page/{1}'.format(klass.base_url, page)
        else:
            return '{0}/tagged/{1}/page/{2}'.format(klass.base_url, urllib.quote(tag.encode('utf8')), page)

    def fetch(self):
        r = requests.get(self.url)
        if not r.ok:
            raise r.raise_for_status()
        self.content = bs4.BeautifulSoup(r.content)

    def all_post(self):
        for div in self.content.find_all('div', class_='post post-type-photo'):
            yield BlogPost(div)

    def next(self):
        span = self.content.find('span', {'class': 'next-page'})
        if not span:
            return None
        link = self.base_url + span.a.attrs['href']
        return BlogPage(url=link, tag=self.tag, page=self.page + 1)

    def previous(self):
        span = self.content.find('span', {'class': 'previous-page'})
        if not span:
            return None
        link = self.base_url + span.a.attrs['href']
        return BlogPage(url=link, tag=self.tag, page=self.page - 1)


class BlogPost(object):
    def __init__(self, content):
        self.content = content
        self.id = int(content.attrs['id'].split('-')[1])
        self._date = None
        self._image_link = None

    @property
    def link(self):
        return self.content.find('div', class_='type').a.attrs['href']

    @property
    def high_preview_link(self):
        return self.content.find('a', class_='high-res').attrs['href']

    @property
    def tag(self):
        return self.content.find('a', class_='single-tag').text

    @property
    def date(self):
        if not self._date:
            date_str = self.content.find('div', class_='date').get_text().strip()
            month, day, year = (int(x) for x in re.split('[^\d]+', date_str))
            self._date = datetime.date(year, month, day)
        return self._date

    @property
    def image_link(self):
        if not self._image_link:
            link = self.content.find('div', class_='post-content').a.attrs['href']
            if link.split('.')[-1].lower() in ('jpg', 'png', 'jpeg', 'bmp'):
                self._image_link = link
            elif re.match('http://animage.tumblr.com/image/\d+', link):
                dom = bs4.BeautifulSoup(requests.get(link).content)
                self._image_link = dom.find('img', {'id': 'content-image'}).attrs['data-src']
            else:
                print_warning('unable to handle link ({}), use high res preview instead'.format(link))
                self._image_link = self.high_preview_link
        return self._image_link


class Crawler(object):
    def __init__(self, tag, output_dir):
        self.tag = tag
        self.output_dir = os.path.expanduser(output_dir)

    def ensure_output_dir(self):
        if not os.path.isdir(self.output_dir):
            os.makedirs(self.output_dir)

    def save_image(self, link, output_path, overwrite=False):
        if os.path.exists(output_path) and not overwrite:
            return
        r = requests.get(link, stream=True)
        if not r.ok:
            raise r.raise_for_status()
        with open(output_path, 'wb') as f:
            for chunk in r.iter_content(chunk_size=1024):
                if chunk:  # filter out keep-alive new chunks
                    f.write(chunk)
            f.flush()

    def process_post(self, post):
        link = post.image_link
        output_path = '{dir}/{year:04d}{month:02d}{day:02d}_{id}.{ext}'.format(
            dir=self.output_dir,
            year=post.date.year,
            month=post.date.month,
            day=post.date.day,
            id=post.id,
            ext=link.split('.')[-1].lower()
        )
        try:
            self.save_image(link, output_path)
        except requests.HTTPError as e:
            print_error('failed to download image {0}: {1}'.format(link, e))
        except Exception as e:
            print_error('failed to save image {0}: {1}'.format(output_path, e))

    def process_blogpage(self, blogpage):
        try:
            blogpage.fetch()
        except requests.HTTPError as e:
            print_error('failed to fetch page: ', e)
            return
        page_name = u'<{0}> page #{1}'.format(blogpage.tag, blogpage.page)
        print(page_name)
        for post in blogpage.all_post():
            try:
                self.process_post(post)
            except Exception as e:
                print_error('error while process post {0}: {1}'.format(post.id, e))
            print('    post', post.id)
            time.sleep(0.5)

    def get_pages(self, pages):
        self.ensure_output_dir()
        for p in pages:
            blogpage = BlogPage(tag=self.tag, page=p)
            self.process_blogpage(blogpage)

    def get_range(self, start_page=1, end_page=None):
        self.ensure_output_dir()
        blogpage = BlogPage(tag=self.tag, page=start_page)
        while blogpage:
            self.process_blogpage(blogpage)
            blogpage = blogpage.next()
            if end_page and blogpage.page > end_page:
                break

