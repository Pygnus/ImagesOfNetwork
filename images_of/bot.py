import logging
import re
from collections import deque
from datetime import datetime

import requests
from praw.helpers import submission_stream
from praw.errors import AlreadySubmitted, APIException, HTTPException

from images_of import settings
from images_of.subreddit import Subreddit

RETRY_MINUTES = 3

class Bot:
    def __init__(self, r, should_post=True):
        self.r = r
        self.should_post = should_post
        self.recent_posts = deque(maxlen=50)

        logging.info('Loading global user blacklist from wiki')
        self.blacklist_users = self._read_blacklist('userblacklist')

        logging.info('Loading global subreddit blacklist from wiki')
        self.blacklist_subs = self._read_blacklist('subredditblacklist')

        self.subreddits = []
        for sub_settings in settings.SLAVE_SUBS:
            sub = Subreddit(**sub_settings)
            sub.load_wiki_blacklist(r)
            self.subreddits.append(sub)

        ext_pattern = '({})$'.format('|'.join(settings.EXTENSIONS))
        self.ext_re = re.compile(ext_pattern, flags=re.IGNORECASE)

        domain_pattern = '^({})$'.format('|'.join(settings.DOMAINS))
        self.domain_re = re.compile(domain_pattern, flags=re.IGNORECASE)

    def _read_blacklist(self, wiki_page):
        content = self.r.get_wiki_page(settings.MASTER_SUB, wiki_page).content_md
        entries = [line.strip().lower()[3:] for line in content.splitlines() if line]
        return set(entries)

    def check(self, post):
        """Check global conditions on a post"""
        if not settings.NSFW_OK and post.over_18:
            return False

        user = post.author.name.lower()
        if user in self.blacklist_users:
            return False

        sub = post.subreddit.display_name.lower()
        if sub in self.blacklist_subs:
            return False

        if self.domain_re.search(post.domain):
            return True

        if self.ext_re.search(post.url):
            return True

        return False


    def crosspost(self, post, sub):
        title = post.title
        comment = '[Original post]({}) by /u/{} in /r/{}'.format(
                post.permalink,
                post.author,
                post.subreddit)

        log_entry = (post.url, sub.name)
        if log_entry in self.recent_posts:
            logging.info('Already posted {} to /r/{}. Skipping.'.format(title, sub.name))
            return
        else:
            self.recent_posts.append(log_entry)
            logging.debug('Added {} to recent posts. Now tracking {} items.'
                          .format(log_entry, len(self.recent_posts)))

        try:
            logging.info('X-Posting into /r/{}: {}'.format(sub.name, title))
            if self.should_post:
                xpost = self.r.submit(
                            sub.name,
                            title,
                            url=post.url,
                            captcha=None,
                            send_replies=True,
                            resubmit=False)

            logging.debug('Commenting: {}'.format(comment))
            if self.should_post:
                xpost.add_comment(comment)

        except AlreadySubmitted:
            logging.info('Already submitted. Skipping.')
        except APIException as e:
            logging.warning(e)
        

    def verify_age(self, post):
        if hasattr(post, 'age_verified'):
            return True

        created = datetime.utcfromtimestamp(post.author.created_utc)
        age = (datetime.utcnow() - created).days
        if age > 2:
            post.age_verified = True
            return True
        return False

    def _do_post(self, post):
        if not self.check(post):
            return

        for sub in self.subreddits:
            if sub.check(post):
                if not self.verify_age(post):
                    return
                self.crosspost(post, sub)

    def run(self):
        stream = submission_stream(self.r, 'all', verbosity=0)

        while True:
            try:
                for post in stream:
                    self._do_post(post)
            except HTTPException as e:
                logging.warning('Reddit is down. Sleeping.')
                logging.debug(e)
                sleep(60 * RETRY_MINUTES)
                continue
            except requests.ConnectionError as e:
                logging.warning('Connection failed - trying again.')
                continue

            logging.warning('Stream ended. Restarting.')
