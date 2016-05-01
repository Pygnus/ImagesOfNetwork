import logging
import re
from textwrap import dedent

import click
from praw.errors import SubredditExists, RateLimitExceeded

from images_of import settings, Reddit


def create_sub(r, sub):
    try:
        logging.info('Attempting to create /r/{}'.format(sub))
        r.create_subreddit(sub, sub)
        logging.info('Created /r/{}'.format(sub))
    except SubredditExists:
        logging.warning('/r/{} exists'.format(sub))


def copy_settings(r, sub, description):
    logging.info('Copying settings from {}'.format(settings.MASTER_SUB))
    sub_settings = r.get_settings(settings.MASTER_SUB)
    logging.debug('{}'.format(sub_settings))

    if description:
        sub_settings['public_description'] = description
    elif sub.startswith('ImagesOf'):
        # XXX this is hardly bulletproof
        place = re.findall('[A-Z][^A-Z]*', sub)[2:].join(' ')
        sub_settings['public_description'] = 'Pictures and images of {}'.format(place)
    else:
        sub_settings['pucblic_description'] = settings.DEFAULT_DESCRIPTION

    logging.info('Copying settings to /r/{}'.format(sub))
    sub_obj = r.get_subreddit(sub)
    try:
        r.set_settings(sub_obj, **sub_settings)
    except RateLimitExceeded:
        # when we change settings on a subreddit,
        # if it's been created within the last 10 minutes,
        # reddit alwasy issues a rate limiting warning
        # informing us about how long we have until we
        # can create a new subreddit, and PRAW interprets
        # this as an error. It's not.
        pass


def invite_mods(r, sub):
    mods = settings.DEFAULT_MODS

    cur_mods = [u.name for u in r.get_moderators(sub)]
    logging.debug('current mods for /r/{}: {}'.format(sub, cur_mods))

    need_mods = [mod for mod in mods if mod not in cur_mods]
    if not need_mods:
        logging.info('All mods already invited.')
        return
    else:
        logging.info('Inviting moderators: {}'.format(need_mods))

    s = r.get_subreddit(sub)
    for mod in need_mods:
        s.add_moderator(mod)

    logging.info('Mods invited.'.format(mod))


def copy_wiki_pages(r, sub):
    for page in settings.WIKI_PAGES:
        logging.info('Copying wiki page "{}"'.format(page))
        content = r.get_wiki_page(settings.MASTER_SUB, page).content_md
        r.edit_wiki_page(sub, page, content=content, reason='Subreddit stand-up')


def setup_flair(r, sub):
    # XXX should this be copied from the master?
    r.configure_flair(sub,
                      flair_enabled=True,
                      flair_position='right',
                      link_flair_enabled=True,
                      link_flair_position='right',
                      flair_self_assign=False)


def add_to_multi(r, sub):
    if not settings.MULTIREDDIT:
        logging.WARNING("No multireddit to add /r/{} to.".format(sub))
        return

    logging.info('Adding /r/{} to /user/{}/m/{}'
                 .format(sub, settings.USERNAME, settings.MULTIREDDIT))

    m = r.get_multireddit(settings.USERNAME, settings.MULTIREDDIT)

    # NOTE: for some reason, at least for this version of PRAW,
    # adding a sub to a multireddit requires us to be logged in.
    r.login()
    m.add_subreddit(sub)


def setup_notifications(r, sub):
    setup = dedent("""\
        {
        "subreddit": "{{subreddit}}",
        "karma": 1,
        "filter-users": [],
        "filter-subreddits": []
        }""")

    logging.info('Requesting notifications about /r/{} from /u/Sub_Mentions'
                 .format(sub))
    r.send_message('Sub_Mentions', 'Action: Subscribe',
                   setup.replace('{{subreddit}}', sub), from_sr=sub)


_start_points = ['creation', 'settings', 'mods', 'wiki', 'flair', 'multireddit', 'notifications']

@click.command()
@click.option('--start-at', type=click.Choice(_start_points),
              help='Where to start the process from.')
@click.option('--only', type=click.Choice(_start_points),
              help='Only run one section of expansion script.')
@click.option('--description', help='Subreddit description.')
@click.argument('sub', required=True)
def main(sub, start_at, only, description):
    """Prop up new subreddit and set it for the network."""

    r = Reddit('Expand {} Network v0.1 /u/{}'
               .format(settings.NETWORK_NAME, settings.USERNAME))
    r.oauth()

    # little helper script to check if we're at or after
    # where we want to start.
    def should_do(point):
        point_idx = _start_points.index(point)
        if only:
            only_idx = _start_points.index(only)
            return only_idx == point_idx
        elif start_at:
            start_idx = _start_points.index(start_at)
            return start_idx <= point_idx
        return True

    if should_do('creation'):
        create_sub(r, sub)

    if should_do('settings'):
        copy_settings(r, sub, description)

    if should_do('mods'):
        invite_mods(r, sub)

    if should_do('wiki'):
        copy_wiki_pages(r, sub)

    if should_do('flair'):
        setup_flair(r, sub)

    if should_do('multireddit'):
        add_to_multi(r, sub)

    if should_do('notifications'):
        setup_notifications(r, sub)

if __name__ == '__main__':
    main()
