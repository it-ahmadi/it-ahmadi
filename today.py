import datetime
from dateutil import relativedelta
import requests
import os
from lxml import etree
import time
import hashlib

# Fine-grained personal access token with All Repositories access:
# Account permissions: read:Followers, read:Starring, read:Watching
# Repository permissions: read:Commit statuses, read:Contents, read:Issues, read:Metadata, read:Pull Requests
# Issues and pull requests permissions not needed at the moment, but may be used in the future
HEADERS = {'authorization': 'token '+ os.environ['ACCESS_TOKEN']}
USER_NAME = os.environ['USER_NAME'] # 'it-ahmadi'
QUERY_COUNT = {'user_getter': 0, 'follower_getter': 0, 'graph_repos_stars': 0, 'recursive_loc': 0, 'graph_commits': 0, 'loc_query': 0}


def simple_request(func_name, query, variables):
    """
    Returns a request, or raises an Exception if the response does not succeed.
    """
    request = requests.post('https://api.github.com/graphql', json={'query': query, 'variables':variables}, headers=HEADERS)
    if request.status_code == 200:
        return request
    raise Exception(func_name, ' has failed with a', request.status_code, request.text, QUERY_COUNT)


def graph_commits(start_date, end_date):
    """
    Uses GitHub's GraphQL v4 API to return my total commit count
    """
    query_count('graph_commits')
    query = '''
    query($start_date: DateTime!, $end_date: DateTime!, $login: String!) {
        user(login: $login) {
            contributionsCollection(from: $start_date, to: $end_date) {
                contributionCalendar {
                    totalContributions
                }
            }
        }
    }'''
    variables = {'start_date': start_date,'end_date': end_date, 'login': USER_NAME}
    request = simple_request(graph_commits.__name__, query, variables)
    return int(request.json()['data']['user']['contributionsCollection']['contributionCalendar']['totalContributions'])


def graph_repos_stars(count_type, owner_affiliation, cursor=None, add_loc=0, del_loc=0):
    """
    Uses GitHub's GraphQL v4 API to return my total repository, star, or lines of code count.
    """
    query_count('graph_repos_stars')
    query = '''
    query ($owner_affiliation: [RepositoryAffiliation], $login: String!, $cursor: String) {
        user(login: $login) {
            repositories(first: 100, after: $cursor, ownerAffiliations: $owner_affiliation) {
                totalCount
                edges {
                    node {
                        ... on Repository {
                            nameWithOwner
                            stargazers {
                                totalCount
                            }
                        }
                    }
                }
                pageInfo {
                    endCursor
                    hasNextPage
                }
            }
        }
    }'''
    variables = {'owner_affiliation': owner_affiliation, 'login': USER_NAME, 'cursor': cursor}
    request = simple_request(graph_repos_stars.__name__, query, variables)
    if request.status_code == 200:
        if count_type == 'repos':
            return request.json()['data']['user']['repositories']['totalCount']
        elif count_type == 'stars':
            return stars_counter(request.json()['data']['user']['repositories']['edges'])


def recursive_loc(owner, repo_name, data, cache_comment, addition_total=0, deletion_total=0, my_commits=0, cursor=None):
    """
    Uses GitHub's GraphQL v4 API and cursor pagination to fetch 100 commits from a repository at a time
    """
    query_count('recursive_loc')
    query = '''
    query ($repo_name: String!, $owner: String!, $cursor: String) {
        repository(name: $repo_name, owner: $owner) {
            defaultBranchRef {
                target {
                    ... on Commit {
                        history(first: 100, after: $cursor) {
                            totalCount
                            edges {
                                node {
                                    ... on Commit {
                                        committedDate
                                    }
                                    author {
                                        user {
                                            id
                                        }
                                    }
                                    deletions
                                    additions
                                }
                            }
                            pageInfo {
                                endCursor
                                hasNextPage
                            }
                        }
                    }
                }
            }
        }
    }'''
    variables = {'repo_name': repo_name, 'owner': owner, 'cursor': cursor}
    request = requests.post('https://api.github.com/graphql', json={'query': query, 'variables':variables}, headers=HEADERS) # I cannot use simple_request(), because I want to save the file before raising Exception
    if request.status_code == 200:
        if request.json()['data']['repository']['defaultBranchRef'] != None: # Only count commits if repo isn't empty
            return loc_counter_one_repo(owner, repo_name, data, cache_comment, request.json()['data']['repository']['defaultBranchRef']['target']['history'], addition_total, deletion_total, my_commits)
        else: return 0
    force_close_file(data, cache_comment) # saves what is currently in the file before this program crashes
    if request.status_code == 403:
        raise Exception('Too many requests in a short amount of time!\nYou\'ve hit the non-documented anti-abuse limit!')
    raise Exception('recursive_loc() has failed with a', request.status_code, request.text, QUERY_COUNT)


def loc_counter_one_repo(owner, repo_name, data, cache_comment, history, addition_total, deletion_total, my_commits):
    """
    Recursively call recursive_loc (since GraphQL can only search 100 commits at a time) 
    only adds the LOC value of commits authored by me
    """
    for node in history['edges']:
        if node['node']['author']['user'] == OWNER_ID:
            my_commits += 1
            addition_total += node['node']['additions']
            deletion_total += node['node']['deletions']

    if history['edges'] == [] or not history['pageInfo']['hasNextPage']:
        return addition_total, deletion_total, my_commits
    else: return recursive_loc(owner, repo_name, data, cache_comment, addition_total, deletion_total, my_commits, history['pageInfo']['endCursor'])


def loc_query(owner_affiliation, comment_size=0, force_cache=False, cursor=None, edges=[]):
    """
    Uses GitHub's GraphQL v4 API to query all the repositories I have access to (with respect to owner_affiliation)
    Queries 60 repos at a time, because larger queries give a 502 timeout error and smaller queries send too many
    requests and also give a 502 error.
    Returns the total number of lines of code in all repositories
    """
    query_count('loc_query')
    query = '''
    query ($owner_affiliation: [RepositoryAffiliation], $login: String!, $cursor: String) {
        user(login: $login) {
            repositories(first: 60, after: $cursor, ownerAffiliations: $owner_affiliation) {
                totalCount
                edges {
                    node {
                        ... on Repository {
                            nameWithOwner
                            defaultBranchRef {
                                target {
                                    ... on Commit {
                                        history(first: 1) {
                                            totalCount
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
                pageInfo {
                    endCursor
                    hasNextPage
                }
            }
        }
    }'''
    variables = {'owner_affiliation': owner_affiliation, 'login': USER_NAME, 'cursor': cursor}
    request = simple_request(loc_query.__name__, query, variables)
    if request.status_code == 200:
        edges += request.json()['data']['user']['repositories']['edges']
        if request.json()['data']['user']['repositories']['pageInfo']['hasNextPage']:
            return loc_query(owner_affiliation, comment_size, force_cache, request.json()['data']['user']['repositories']['pageInfo']['endCursor'], edges)
        return cache_builder(edges, comment_size, force_cache)
    raise Exception('loc_query() has failed with a', request.status_code, request.text, QUERY_COUNT)


def cache_builder(edges, comment_size, force_cache):
    """
    Checks each repository in edges to see if it has been updated since the last time it was cached.
    If it has, it will update the cache file with the new data.
    """
    filename = 'cache/'+hashlib.sha256(USER_NAME.encode('utf-8')).hexdigest()+'.txt'
    if not os.path.exists(filename):
        force_cache = True
        print('   Cache file not found, creating new cache file.')
    if force_cache:
        with open(filename, 'w') as f:
            f.writelines(['This is a cache file for '+USER_NAME+'\'s GitHub data.\n', 'There are a total of 6 space-seperated values per line (7 after adding my commits value):\n', 'repo_hash TOTAL_COMMITS MY_COMMITS_VALUE ADDITIONS DELETIONS\n', '\n', 'The final line indicates the number of commits on repositories that have been deleted, and has an extra value at the end:\n', 'repo_hash TOTAL_COMMITS MY_COMMITS_VALUE ADDITIONS DELETIONS CONTRIBUTED_REPOS\n', '\n'])
        with open(filename, 'a') as f:
            added_loc, deleted_loc, contributed_repos = 0, 0, 0
            for node in edges:
                contributed_repos += 1
                repo_hash = hashlib.sha256(node['node']['nameWithOwner'].encode('utf-8')).hexdigest()
                if node['node']['defaultBranchRef'] != None:
                    loc_data = recursive_loc(node['node']['nameWithOwner'].split('/')[0], node['node']['nameWithOwner'].split('/')[1], f, ['','','','','','',''])
                    f.write(repo_hash + ' ' + str(node['node']['defaultBranchRef']['target']['history']['totalCount']) + ' ' + str(loc_data[2]) + ' ' + str(loc_data[0]) + ' ' + str(loc_data[1]) + '\n')
                    added_loc += loc_data[0]
                    deleted_loc += loc_data[1]
                else:
                    f.write(repo_hash + ' 0 0 0 0\n')
            f.write('00000000000000000000000000000000 0 0 0 0 ' + str(contributed_repos) + '\n')
        return [added_loc, deleted_loc, added_loc - deleted_loc, 0, False]
    return add_archive(comment_size)


def add_archive(comment_size=7):
    """
    Adds the archived data to the current data
    This is only relevant for me (Mohammad Hossein Ahmadi), because I have contributed to repositories that have since been deleted
    """
    added_loc, deleted_loc, added_commits, contributed_repos = 0, 0, 0, 0
    filename = 'cache/'+hashlib.sha256(USER_NAME.encode('utf-8')).hexdigest()+'.txt'
    with open(filename, 'r') as f:
        old_data = f.readlines()
    old_data = old_data[comment_size:]
    for line in old_data[:-1]:
        repo_hash, total_commits, my_commits, *loc = line.split()
        added_loc += int(loc[0])
        deleted_loc += int(loc[1])
        if (my_commits.isdigit()): added_commits += int(my_commits)
    added_commits += int(old_data[-1].split()[4][:-1])
    return [added_loc, deleted_loc, added_loc - deleted_loc, added_commits, contributed_repos]

def force_close_file(data, cache_comment):
    """
    Forces the file to close, preserving whatever data was written to it
    This is needed because if this function is called, the program would've crashed before the file is properly saved and closed
    """
    filename = 'cache/'+hashlib.sha256(USER_NAME.encode('utf-8')).hexdigest()+'.txt'
    with open(filename, 'w') as f:
        f.writelines(cache_comment)
        f.writelines(data)
    print('There was an error while writing to the cache file. The file,', filename, 'has had the partial data saved and closed.')


def stars_counter(data):
    """
    Count total stars in repositories owned by me
    """
    total_stars = 0
    for node in data: total_stars += node['node']['stargazers']['totalCount']
    return total_stars


def svg_overwrite(filename, commit_data, star_data, repo_data, contrib_data, follower_data, loc_data):
    """
    Parse SVG files and update elements with commits, stars, repositories, and lines written
    """
    tree = etree.parse(filename)
    root = tree.getroot()
    justify_format(root, 'commit_data', commit_data, 22)
    justify_format(root, 'star_data', star_data, 14)
    justify_format(root, 'repo_data', repo_data, 6)
    justify_format(root, 'contrib_data', contrib_data)
    justify_format(root, 'follower_data', follower_data, 10)
    justify_format(root, 'loc_data', loc_data[2], 9)
    justify_format(root, 'loc_add', loc_data[0])
    justify_format(root, 'loc_del', loc_data[1], 7)
    tree.write(filename, encoding='utf-8', xml_declaration=True)


def justify_format(root, element_id, new_text, length=0):
    """
    Updates and formats the text of the element, and modifes the amount of dots in the previous element to justify the new text on the svg
    """
    if isinstance(new_text, int):
        new_text = f"{'{:,}'.format(new_text)}"
    new_text = str(new_text)
    find_and_replace(root, element_id, new_text)
    just_len = max(0, length - len(new_text))
    if just_len <= 2:
        dot_map = {0: '', 1: ' ', 2: '. '}
        dot_string = dot_map[just_len]
    else:
        dot_string = ' ' + ('.' * just_len) + ' '
    find_and_replace(root, f"{element_id}_dots", dot_string)


def find_and_replace(root, element_id, new_text):
    """
    Finds the element in the SVG file and replaces its text with a new value
    """
    element = root.find(f".//*[@id='{element_id}']")
    if element is not None:
        element.text = new_text


def commit_counter(comment_size):
    """
    Counts up my total commits, using the cache file created by cache_builder.
    """
    total_commits = 0
    filename = 'cache/'+hashlib.sha256(USER_NAME.encode('utf-8')).hexdigest()+'.txt' # Use the same filename as cache_builder
    with open(filename, 'r') as f:
        data = f.readlines()
    cache_comment = data[:comment_size] # save the comment block
    data = data[comment_size:] # remove those lines
    for line in data:
        total_commits += int(line.split()[2])
    return total_commits


def user_getter(username):
    """
    Returns the account ID and creation time of the user
    """
    query_count('user_getter')
    query = '''
    query($login: String!){
        user(login: $login) {
            id
            createdAt
        }
    }'''
    variables = {'login': username}
    request = simple_request(user_getter.__name__, query, variables)
    return {'id': request.json()['data']['user']['id']}, request.json()['data']['user']['createdAt']

def follower_getter(username):
    """
    Returns the number of followers of the user
    """
    query_count('follower_getter')
    query = '''
    query($login: String!){
        user(login: $login) {
            followers {
                totalCount
            }
        }
    }'''
    request = simple_request(follower_getter.__name__, query, {'login': username})
    return int(request.json()['data']['user']['followers']['totalCount'])


def query_count(funct_id):
    """
    Counts how many times the GitHub GraphQL API is called
    """
    global QUERY_COUNT
    QUERY_COUNT[funct_id] += 1


def perf_counter(funct, *args):
    """
    Calculates the time it takes for a function to run
    Returns the function result and the time differential
    """
    start = time.perf_counter()
    funct_return = funct(*args)
    return funct_return, time.perf_counter() - start


def formatter(query_type, difference, funct_return=False, whitespace=0):
    """
    Prints a formatted time differential
    Returns formatted result if whitespace is specified, otherwise returns raw result
    """
    print('{:<23}'.format('   ' + query_type + ':'), sep='', end='')
    print('{:>12}'.format('%.4f' % difference + ' s ')) if difference > 1 else print('{:>12}'.format('%.4f' % (difference * 1000) + ' ms'))
    if whitespace:
        return f"{'{:,}'.format(funct_return): <{whitespace}}"
    return funct_return

if __name__ == '__main__':
    print('Calculation times:')
    user_data, user_time = perf_counter(user_getter, USER_NAME)
    OWNER_ID, acc_date = user_data
    formatter('account data', user_time)
    total_loc, loc_time = perf_counter(loc_query, ['OWNER', 'COLLABORATOR', 'ORGANIZATION_MEMBER'], 7)
    formatter('LOC (cached)', loc_time) if total_loc[-1] else formatter('LOC (no cache)', loc_time)
    commit_data, commit_time = perf_counter(commit_counter, 7)
    star_data, star_time = perf_counter(graph_repos_stars, 'stars', ['OWNER'])
    repo_data, repo_time = perf_counter(graph_repos_stars, 'repos', ['OWNER'])
    contrib_data, contrib_time = perf_counter(graph_repos_stars, 'repos', ['OWNER', 'COLLABORATOR', 'ORGANIZATION_MEMBER'])
    follower_data, follower_time = perf_counter(follower_getter, USER_NAME)

    # several repositories that I've contributed to have since been deleted.
    if OWNER_ID == {'id': 'MDQ6VXNlcjgyNjE1MTgz'}: # only calculate for user mohammad hossein
        archived_data = add_archive()
        for index in range(len(total_loc)-1):
            total_loc[index] += archived_data[index]
        contrib_data += archived_data[-1]
        commit_data += int(archived_data[-2])

    for index in range(len(total_loc)-1): total_loc[index] = '{:,}'.format(total_loc[index]) # format added, deleted, and total LOC

    svg_overwrite('it-ahmadi.svg', commit_data, star_data, repo_data, contrib_data, follower_data, total_loc[:-1])
    svg_overwrite('it-ahmadi.svg', commit_data, star_data, repo_data, contrib_data, follower_data, total_loc[:-1])

    # move cursor to override 'Calculation times:' with 'Total function time:' and the total function time, then move cursor back
    print('\033[F\033[F\033[F\033[F\033[F\033[F\033[F',
        '{:<21}'.format('Total function time:'), '{:>11}'.format('%.4f' % (user_time + loc_time + commit_time + star_time + repo_time + contrib_time)),
        ' s \033[E\033[E\033[E\033[E\033[E\033[E\033[E', sep='')

    print('Total GitHub GraphQL API calls:', '{:>3}'.format(sum(QUERY_COUNT.values())))
    for funct_name, count in QUERY_COUNT.items(): print('{:<28}'.format('   ' + funct_name + ':'), '{:>6}'.format(count))