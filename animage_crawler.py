#!/usr/bin/env python

import sys
import os
import datetime
import time
import re
import urllib

import requests
import bs4
from clint.textui import puts, indent
from clint.textui import puts_err, colored


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
            if isinstance(tag, unicode):
                tag = tag.encode('utf8')
            return '{0}/tagged/{1}/page/{2}'.format(klass.base_url, urllib.quote(tag), page)

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
        if not hasattr(self, '_date'):
            date_str = self.content.find('div', class_='date').get_text().strip()
            month, day, year = (int(x) for x in re.split('[^\d]+', date_str))
            self._date = datetime.date(year, month, day)
        return self._date

    @property
    def image_link(self):
        if not hasattr(self, '_image_link'):
            self._image_link, self._image_link_type = self.analyze_image_link()
        return self._image_link

    @property
    def image_link_type(self):
        if not hasattr(self, '_image_link_type'):
            self._image_link, self._image_link_type = self.analyze_image_link()
        return self._image_link_type

    IMG_LINK_PATTERN = re.compile('http://animage.tumblr.com/image/\d+')

    def analyze_image_link(self):
        link = self.content.find('div', class_='post-content').a.attrs['href']
        if link.split('.')[-1].lower() in ('jpg', 'png', 'jpeg', 'bmp'):
            image_link = link
            link_type = 'POST_LINK'
        elif self.IMG_LINK_PATTERN.match(link):
            dom = bs4.BeautifulSoup(requests.get(link).content)
            image_link = dom.find('img', {'id': 'content-image'}).attrs['data-src']
            link_type = 'DATA_SRC'
        else:
            image_link = self.high_preview_link
            link_type = 'HIGH_PREVIEW'
        return image_link, link_type

    def save_image(self, output_path):
        r = requests.get(self.image_link, stream=True)
        if not r.ok:
            raise r.raise_for_status()
        with open(output_path, 'wb') as f:
            for chunk in r.iter_content(chunk_size=1024):
                if chunk:  # filter out keep-alive new chunks
                    f.write(chunk)
            f.flush()


class Crawler(object):
    class Error(Exception):
        __module__ = 'Crawler'

    def __init__(self, tag, output_dir, status_stream=sys.stdout, err_stream=sys.stderr):
        self.tag = tag
        self.output_dir = os.path.expanduser(output_dir)
        self._pre_out_dir = None
        self.stream = status_stream
        self.err_stream = err_stream

    def ensure_output_dir(self, path=None):
        if not path:
            path = self.output_dir
        if self._pre_out_dir == path:
            return
        try:
            os.makedirs(path)
        except OSError:
            if not os.path.isdir(path):
                raise
        self._pre_out_dir = path

    def format_output_path(self, post):
        link = post.image_link
        dirname = '{dir}/{year:04d}_{month:02d}'.format(
            dir=self.output_dir,
            year=post.date.year,
            month=post.date.month,
            day=post.date.day,
        )
        filename = '{id}.{ext}'.format(
            id=post.id,
            ext=link.split('.')[-1].lower()
        )
        return dirname, filename

    def _puts(self, msg, newline=True):
        if self.stream:
            puts(msg, newline=newline, stream=self.stream.write)

    def _puts_err(self, msg, newline=True):
        if self.err_stream:
            puts(msg, newline=newline, stream=self.err_stream.write)
        else:
            self._puts(msg, newline=newline)

    INDENT = 4

    def process_post(self, post, overwrite=False):
        # write status
        self._puts('post {}'.format(post.id), newline=False)
        # compose output path
        dirname, filename = self.format_output_path(post)
        output_path = os.path.join(dirname, filename)
        # check overwritting and write status if skipped
        if not overwrite and os.path.exists(output_path):
            self._puts(' skipped ({0})'.format(output_path))
            return False, output_path
        # get the image
        try:
            self.ensure_output_dir(os.path.dirname(output_path))
            post.save_image(output_path)
        except requests.RequestException as e:
            raise self.Error('failed to download image {0}: {1}'.format(post.image_link, e)), None, sys.exc_info()[2]
        except Exception as e:
            raise self.Error('failed to save image {0}: {1}'.format(output_path, e)), None, sys.exc_info()[2]
        # mark status as 'saved'
        if post.image_link_type == 'HIGH_PREVIEW':
            self._puts(' saved (HIGH_PREVIEW)')
            with indent(self.INDENT):
                self._puts('WARN: unable to handle image link {}'.format(post.image_link))
        else:
            self._puts(' saved')
        return True, output_path

    def process_blogpage(self, blogpage, overwrite):
        try:
            blogpage.fetch()
        except requests.HTTPError as e:
            raise self.Error('failed to fetch page: {0}'.format(e)), None, sys.exc_info()[2]
        self._puts('<{0}> page #{1}'.format(blogpage.tag, blogpage.page))
        anything_saved = False
        for post in blogpage.all_post():
            with indent(self.INDENT):
                try:
                    saved, path = self.process_post(post, overwrite)
                    if saved:
                        time.sleep(0.5)
                    anything_saved |= saved
                except Exception as e:
                    self._puts(' ERROR: {}'.format(e))
        return anything_saved

    def get_pages(self, pages):
        for p in pages:
            blogpage = BlogPage(tag=self.tag, page=p)
            try:
                self.process_blogpage(blogpage, overwrite=False)
            except self.Error as e:
                self._puts_err('ERROR: ' + str(e))

    def get_range(self, start_page=1, end_page=None, update_only=False):
        blogpage = BlogPage(tag=self.tag, page=start_page)
        is_overwrite = not update_only
        while blogpage:
            try:
                anything_saved = self.process_blogpage(blogpage, overwrite=is_overwrite)
                if update_only and not anything_saved:
                    self._puts('update finished on page {}'.format(blogpage.page), stream=self.stream)
                    return
            except self.Error as e:
                self._puts_err('ERROR: ' + str(e))
            blogpage = blogpage.next()
            if end_page and blogpage.page > end_page:
                break


from argtools import command, argument


@command
@argument('-t', '--tag')
@argument('-o', '--output_dir', required=True)
@argument('-p', '--page', dest='pages', type=int, nargs='*')
@argument('-s', '--start', type=int, help='get images from this page')
@argument('-e', '--end', type=int, help='get images till this page, must be used with --start')
@argument('-u', '--update', action='store_true', help='do not download again if the image is already got before')
def main(args):
    if args.end and not args.start:
        puts_err('start pages not gived')
        sys.exit()
    if not os.path.isdir(args.output_dir):
        puts_err(colored.red('output dir {0} does not exist, auto create it.'.format(args.output_dir)))
        os.makedirs(args.output_dir)
    crawler = Crawler(args.tag, args.output_dir)
    if args.start:
        crawler.get_range(args.start, args.end, args.update)
    if args.pages:
        crawler.get_pages(args.pages)


if __name__ == '__main__':
    command.run()

