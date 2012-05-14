import gevent
from gevent import monkey
monkey.patch_all()

import requests
import json
import time
import random
from pyquery import PyQuery as pq
from collections import namedtuple

import dabase
from dabase import Dabblet, DabChoice

API_URL = "http://en.wikipedia.org/w/api.php"

class WikiException(Exception): pass


Page = namedtuple("Page", "title, req_title, pageid, revisionid, revisiontext, images, is_parsed, fetch_date")

DabOption = namedtuple("DabOption", "title, text, dab_title")

def api_req(action, params=None, raise_exc=False, **kwargs):
    all_params = {'format': 'json',
                  'servedby': 'true'}
    all_params.update(kwargs)
    all_params.update(params)
    all_params['action'] = action
    
    resp = requests.Response()
    resp.results = None
    try:
        if action == 'edit':
            resp = requests.post(API_URL, params=all_params)
        else:
            resp = requests.get(API_URL, params=all_params)
    except Exception as e:
        if raise_exc:
            raise
        else:
            resp.error = e
            return resp
    
    try:
        resp.results = json.loads(resp.text)
        resp.servedby = resp.results.get('servedby')
        # TODO: warnings?
    except Exception as e:
        if raise_exc:
            raise
        else:
            resp.error = e
            return resp

    mw_error = resp.headers.get('MediaWiki-API-Error')
    if mw_error:
        error_str = mw_error
        error_obj = resp.results.get('error')
        if error_obj and error_obj.get('info'):
            error_str += ' ' + error_obj.get('info')
        if raise_exc:
            raise WikiException(error_str)
        else:
            resp.error = error_str
            return resp

    return resp


def get_category(cat_name, count=500):
    params = {'list': 'categorymembers', 
              'cmtitle': 'Category:'+cat_name, 
              'prop': 'info', 
              'cmlimit': count}
    return api_req('query', params)
    
def get_dab_page_ids(date=None, count=500):
    cat_res = get_category("Articles_with_links_needing_disambiguation_from_June_2011", count)
    # TODO: Continue query?
    # TODO: Get subcategory of Category:Articles_with_links_needing_disambiguation
    return [ a['pageid'] for a in 
             cat_res.results['query']['categorymembers'] ]


def get_articles(page_ids=None, titles=None, parsed=True, follow_redirects=False, **kwargs):
    ret = []
    params = {'prop':    'revisions',  
              'rvprop':  'content|ids' }

    if page_ids:
        if not isinstance(page_ids, (str,unicode)):
            try:
                page_ids = "|".join([str(p) for p in page_ids])
            except:
                pass
        params['pageids'] = str(page_ids)
    elif titles:
        if not isinstance(titles, (str,unicode)):
            try:
                titles = "|".join([str(t) for t in titles])
            except:
                print "Couldn't join: ",repr(titles)
        params['titles'] = titles
    else:
        raise Exception('You need to pass in a page id or a title.')

    if parsed:
        params['rvparse'] = 'true'
    if follow_redirects:
        params['redirects'] = 'true'

    parse_resp = api_req('query', params, **kwargs)
    if parse_resp.results:
        try:
            pages = parse_resp.results['query']['pages'].values()
            redirect_list = parse_resp.results['query'].get('redirects', [])
        except:
            print "Couldn't get_articles() with params: ", params
            return ret

        redirects = dict([ (r['to'],r['from']) for r in redirect_list ])
        for page in pages:
            title = page['title']
            pa = Page( pageid = page['pageid'],
                       title  = title,
                       req_title  = redirects.get(title, title),
                       revisionid = page['revisions'][0]['revid'],
                       revisiontext = page['revisions'][0]['*'],
                       is_parsed = parsed,
                       fetch_date = time.time())
            ret.append(pa)
    return ret


def is_fixable_dab_link(parsed_page):
    # Check for redirect
    # Check for hat notes
    pass


def get_dab_choices(dabblets): # side effect-y..
    ret = []
    if not dabblets:
        return ret
    dab_map = dict([(d.title, d) for d in dabblets])
    dab_pages = get_articles(titles=dab_map.keys(), follow_redirects=True)

    for dp in dab_pages:
        dabblet  = dab_map.get(dp.req_title) # :/ (worried about special characters)
        dab_text = dp.revisiontext
        
        d = pq(dab_text)
        d('table#toc').remove()
        liasons = set([ d(a).parents('li')[-1] for a in d('li a') ])
        for lia in liasons:
            # TODO: better heuristic than ":first" link?
            title = d(lia).find('a:first').attr('title') 
            text = lia.text_content().strip()
            ret.append(DabChoice(dabblet=dabblet,
                                 title=title, 
                                 text=text))
    
    return ret


def get_context(dab_a):
    d = dab_a(dab_a.parents()[0])
    d(dab_a).addClass('dab-link')
    link_parents = dab_a.parents()
    cand_contexts = [ p for p in link_parents 
                      if p.text_content() and len(p.text_content().split()) > 30 ]
    chosen_context = cand_contexts[-1]
    d(chosen_context).addClass('dab-context')
    # add upperbound/wrapping div
    return d(chosen_context)
    
def get_dabblets(parsed_page):
    "Call with a Page object, the type you'd get from get_articles()"
    ret = []
    d = pq(parsed_page.revisiontext)
    page_title = parsed_page.title

    images_found = [img.attrib['src'] for img in d('.thumbimage')][:3]

    dab_link_markers = d('span:contains("disambiguation needed")')
    for i, dlm in enumerate(dab_link_markers):
        try:
            dab_link = d(dlm).parents("sup")[0].getprevious() # TODO: remove extra d?
            dab_link = d(dab_link)
        except Exception as e:
            print 'nope', e
            continue
        if dab_link.is_('a'):
            dab_title = dab_link.attr('title')
            context = get_context(dab_link)

            ret.append( Dabblet.from_page(dab_title, 
                                          context.outerHtml(), 
                                          parsed_page, 
                                          i,
                                          '||'.join(images_found)))
            
    return ret

def get_random_dabblets(count=2):
    dabblets = []
    page_ids = random.sample(get_dab_page_ids(count=count*2), count)
    articles = get_articles(page_ids)
    dabblets.extend(sum([get_dabblets(a) for a in articles], []))
    return dabblets

def save_a_bunch(count=1000):
    import time
    P_PER_CALL = 4
    db_name = 'abunch'
    dabase.init(db_name)
    dabblets = []

    page_ids = get_dab_page_ids(count=count)

    print 'fetching', len(page_ids), 'articles...'
    start = time.time()
    ajobs = [gevent.spawn(get_articles, page_ids[i:i+P_PER_CALL]) for i in range(0, len(page_ids), P_PER_CALL)]
    print 'using', len(ajobs), 'green threads.'
    gevent.joinall(ajobs, timeout=30)
    print 'fetch done (t+', time.time() - start, 'seconds)'
    for aj in ajobs:
        articles = aj.value
        if not articles:
            continue
        dabblets.extend(sum([get_dabblets(a) for a in articles], []))

    get_dab_choices(dabblets[:1])

    all_choices = []
    print 'fetching choices for', len(dabblets), 'Dabblets.'
    choices_start = time.time()

    cjobs = [ gevent.spawn(get_dab_choices, dabblets[i:i+P_PER_CALL])
              for i in
              range(0, len(dabblets), P_PER_CALL) ]

    print 'using', len(cjobs), 'green threads.'
    gevent.joinall(cjobs, timeout=30)
    print 'fetching choices done (t+', time.time() - choices_start, 'seconds)'
    for cj in cjobs:
        choices = cj.value
        if not choices:
            continue
        all_choices.extend(choices)

    for d in dabblets:
        d.save()
    for c in all_choices:
        c.save()

    end = time.time()
    fetched_len = len(dabblets)

    print len(dabblets), 'Dabblets saved to', db_name, 'in', end-start, 'seconds'
    print len(set([d.title for d in dabblets])), 'unique titles'
    print len(set([d.source_title for d in dabblets])), 'unique source pages'
    print len(all_choices), 'dabblet choices fetched and saved.'

    print Dabblet.select().count(), 'total records in database'
    print len(set([d.title for d in Dabblet.select()])), 'unique titles in database'

    return dabblets

def test():
    print 'getting one article by ID'
    pid_article = get_articles(4269567, raise_exc=True)
    assert len(pid_article) > 0
    print 'getting one article by list of IDs (list of one)'
    pid_articles = get_articles([4269567], raise_exc=True)
    assert len(pid_articles) > 0

if __name__ == '__main__':
    dabblets = save_a_bunch(50)
    import pdb;pdb.set_trace()

