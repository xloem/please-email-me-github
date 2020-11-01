#!/usr/bin/env python3

# active-goal is listed here, immediately below this line <-

# -> when adding a comment to a bug, we'll want a way to check if it is already added

# github object id prefixes (not event id) by json part:
# owner: 04:User
# user: 04:User
# member: 04:User
# issue: 05:Issue
# labels[]: 05:Label (yes, same number as issue)
# license: 07:License
# forkee: 010:Repository
# repo: 010:Repository
# pull_request: 011:PullRequest
# comment: 012:IssueComment
# comment: 024:PullRequestReviewComment

import argparse
import base64
from dataclasses import dataclass
import dateutil.parser
import functools
import json
import os
import subprocess
from tqdm import tqdm
import tempfile

print('this uses xloem\'s fork of git-bug that lets manual details be supplied')
print('https://github.com/xloem/git-bug/tree/manual-details\n')

# NEXT: load all users first, since they can be referred to before their details are available

def run(cmd, *args, replies = None, path = None):
	try:
		proc = subprocess.Popen([*cmd.split(' '), *(str(arg) for arg in args)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=path)
	except Exception as e:
		print(cmd, *args)
		raise e
	while replies and len(replies):
		key = bytes(next(iter(replies)), 'utf-8')
		buf = proc.stdout.read(len(key))
		while buf != key:
			print(buf, key)
			buf = buf[1:] + proc.stdout.read(1)
		key = key.decode()
		value = replies[key]
		proc.stdin.write(bytes(value + '\n', 'utf-8'))
		proc.stdin.flush()
		print(buf.decode(), value)
		del replies[key]
	for line in proc.stdout:
		if proc.poll():
			break
		yield line[:-1].decode()
	if proc.wait():
		print(proc.stdout.read().decode())
		raise subprocess.CalledProcessError(proc.returncode, proc.args)

# we'd like to use it in with
# so, we would return NamedTemporaryFile?
## no, that makes it close.  we want it to delete.
class string2tempfn:
	def __init__(self, data):
		with tempfile.NamedTemporaryFile('w',delete=False) as file:
			file.write(data)
			self.filename = file.name
	def __enter__(self):
		return self.filename
	def __exit__(self, *args):
		os.remove(self.filename)
	

@dataclass
class User:
	login : str
	fullname : str
	email : str
	avatar : str
	json : str
	githubid : str
	hash : str = None

@dataclass
class Bug:
	title : str
	body : str
	user : str
	time : int
	status: str
	githuburl : str
	json : str
	githubid : str
	hash : str = None
	modtime : int = 0

@dataclass
class StatusChange:
	status : str
	bug : str
	json : str
	hash : str = None

class Users:
	def __init__(self, path = None):
		if path is None:
			path = os.curdir
		self.path = path
		self.namemappings = {}
		userlines = run('git bug user ls', path = self.path)
		# there's a bug here where 2 bogus users are read on first
		# run.  haven't looked at it.
		userlines = [*userlines]
		if len(userlines) == 2:
			print('is this the bug?', userlines)
		for line in tqdm(userlines, "Mapping imported users", unit='user'):
			hash, name = line.split(' ', 1)
			if name[-1] == ')':
				login = name.split('(')[-1][:-1]
			else:
				login = name
			if login in self.namemappings:
				print(line)
				raise KeyError('duplicate user', login)
			self.namemappings[login] = hash
	def adopt(self, user):
		if user in self:
			user = self[user]
		run('git bug user adopt', user.hash, path = self.path)
	def __getitem__(self, login):
		if login is None:
			login = 'unknown'
			if login not in self.namemappings:
				self[login] = User(login, 'Unknown User', 'unknown@localhost.localdomain', '', {})
		hash = self.namemappings[login]
		metadata = [*run('git bug user --field metadata', hash, path = self.path)]
		metadata = {k:v for k,v in zip(metadata[0::2],metadata[1::2])}
		if 'gharchive-json' not in metadata:
			raise Exception('json not found in metadata',login,metadata)
		user = User(
			*run('git bug user --field login', hash, path = self.path),
			*run('git bug user --field name', hash, path = self.path),
			*run('git bug user --field email', hash, path = self.path),
			*run('git bug user --field avatarUrl', hash, path = self.path),
			metadata['gharchive-json'],
			metadata['github-id'],
			*run('git bug user --field id', hash, path = self.path)
		)
		return user
	def __contains__(self, login):
		return login in self.namemappings
	def __setitem__(self, login, user):
		if login in self.namemappings:
			raise KeyError('duplicate user', login)
		if not user.hash:
			with string2tempfn(user.json) as jsonfilename:
				user.hash = [*run(
					'git bug user create',
					'--login', user.login,
					'--name', user.fullname,
					'--email', user.email,
					'--avatar', user.avatar,
					'--metadata', 'github-login=' + user.login,
					'--metadata', 'github-id=' + user.githubid,
					'--metadatafile', 'gharchive-json=' + jsonfilename,
					path = self.path
				)][-1]
		self.namemappings[user.login] = user.hash

class Bugs:
	def __init__(self, path = None):
		if path is None:
			path = os.curdir
		self.path = path
		self.bugmappings = {}
		# likely to be too slow
		bugs = json.loads('\n'.join(run('git bug ls --format json', path = self.path)))
		for bug in tqdm(bugs, "Mapping imported bugs", unit='bug'):
			hash = bug['id']
			urlparts = bug['metadata']['github-url'].split('/')
			number = int(urlparts[-1])
			self.bugmappings[number] = hash
	def setstatus(self, bug, time, status, json):
		if bug in self.bugmappings:
			bug = self.bugmappings[bug]
		if isinstance(bug, Bug):
			bug = bug.hash
		with string2tempfn(json) as jsonfilename:
			run(
				'git bug status', status,
				bug,
				'--time', time,
				'--metadatafile', 'gharchive-json=' + jsonfilename,
				path = self.path
			)
	def addcomment(self, bug, time, message, githubid, githuburl, json):
		if bug in self.bugmappings:
			bug = self.bugmappings[bug]
		if isinstance(bug, Bug):
			bug = bug.hash
		with string2tempfn(json) as jsonfilename, string2tempfn(message) as messagefilename:
			run(
				'git bug comment add',
				bug,
				'--time', time,
				'--file', messagefilename,
				'--metadata', 'github-url=' + githuburl,
				'--metadata', 'github-id=' + githubid,
				'--metadata', 'origin=github',
				'--metadatafile', 'gharchive-json=' + jsonfilename,
				path = self.path
			)
	def __getitem__(self, number):
		hash = self.bugmappings[number]
		bug = json.loads('\n'.join(run('git bug show --format json', hash, path = self.path)))
		metadata = [*run('git bug show --field creationMetadata', hash, path = self.path)]
		metadata = {k:v for k,v in zip(metadata[0::2],metadata[1::2])}
		if not 'github-id' in metadata:
			metadata['github-id'] = None
		return Bug(
			bug['title'],
			bug['comments'][0]['message'],
			bug['author']['id'],
			bug['create_time']['timestamp'],
			bug['status'],
			metadata['github-url'],
			metadata['gharchive-json'],
			metadata['github-id'],
			bug['id'],
			bug['edit_time']['timestamp']
		)
	def __contains__(self, number):
		return number in self.bugmappings
	def __setitem__(self, number, bug):
		if number in self.bugmappings:
			raise KeyError('duplicate bug', bug)
		if not bug.hash:
			usermap.adopt(bug.user)
			with string2tempfn(bug.json) as jsonfilename, string2tempfn(bug.body) as bodyfilename:
				args = [
					'git bug add',
					'--title', bug.title,
					'--file', bodyfilename,
					'--time', int(bug.time.timestamp()),
					'--metadata', 'github-url=' + bug.githuburl,
					'--metadata', 'github-id=' + bug.githubid,
					'--metadata', 'origin=github',
					'--metadatafile', 'gharchive-json=' + jsonfilename,
				]
				bug.hash = [*run(
					*args,
					path = self.path
				)][-1].split(' ',1)[0]
		self.bugmappings[number] = bug.hash

usermap = Users()
bugmap = Bugs()
#issuemap = {}
eventmap = {}


parser = argparse.ArgumentParser()
parser.add_argument("dir", help="folder containing only gharchive newline-delimited .json files")
args = parser.parse_args()

def parsedate(item):
	return dateutil.parser.isoparse(item.replace('/','-').replace(' -','-').replace(' +','+'))
def id2githubid(type, id):
	return base64.b64encode(bytes(type + str(id), 'utf-8')).decode()

class EventsDir:
	def __init__(self, dir):
		self.dir = dir
		self.filenames = [*os.listdir(self.dir)]
		if len(self.filenames):
			print('WARNING: this is just a work in progress and may make new issues and users in your git-bug repository every time it is run.')
		self.filenames.sort()
		self.filecount = len(self.filenames)
	def __iter__(self):
		lastevent = None
		filenum = 0
		# so we take two files
		# sort them together
		# and then process only 1 file's length
		for filename, nextfilename in zip(self.filenames, (*self.filenames[1:],None)):
			# two files are sorted together
			# and then only data equivalent in length to the first file kept
			# this is a way to handle creation dates being out of order
			with open(os.path.join(self.dir,filename)) as file:
				events = [{'json': line[:-1], **json.loads(line)} for line in file.readlines()]
			cutoff = len(events)
			if nextfilename:
				with open(os.path.join(self.dir,nextfilename)) as nextfile:
					events.extend([{'json': line[:-1], **json.loads(line)} for line in nextfile.readlines()])
			def eventcmp(a, b):
				if 'id' in a and 'id' in b:
					a, b = (int(e['id']) for e in (a, b))
				else:
					a, b = (parsedate(e['created_at']) for e in (a, b))
				return (a > b) - (a < b)
			events.sort(key = functools.cmp_to_key(eventcmp))
			events = events[:cutoff]

			eventcount = len(events)
			eventnum = 0
			for event in events:
				eventnum += 1
				self.mutate_event(event)
				if lastevent is not None:
					if eventcmp(event, lastevent) < 0:
						raise AssertionError('out of order events', filename, 'previous:', (lastevent['id'],lastevent['created_at']), 'now:', (event['id'],event['created_at']))
				event['fileprogress'] = int((filenum + eventnum / eventcount)*1000)/1000
				yield event
				lastevent = event
			filenum += 1
	def mutate_event(self, event):
		if 'actor_attributes' in event:
			event['actor'] = event['actor_attributes']
			del event['actor_attributes']
		# payload issue has a 'user' field sometimes, which contains node_id of user
		# it could be merged with actor to get node_id, dunno
		event['actor'] = self.translate_actor(event['actor'])
		if 'payload' in event:
			event['payload'] = self.translate_payload(event['payload'])
		if 'repo' in event:
			del event['repo']
		if 'repository' in event:
			del event['repository']
		event['created_at_datetime'] = parsedate(event['created_at'])
	def translate_actor(self, actor):
		if not isinstance(actor, dict):
			return { 'login': 'actor' }
		actor = {**actor}
		if 'node_id' not in actor and 'id' in actor:
			actor['node_id'] = id2githubid('04:User', actor['id'])
		if not 'avatar_url' in actor:
			if 'gravatar_id' in actor:
				actor['avatar_url'] = 'https://gravatar.com/avatar/' + actor['gravatar_id']
			else:
				actor['avatar_url'] = ''
		return actor
	def translate_payload(self, payload):
		payload = {**payload}
		if 'issue' in payload and isinstance(payload['issue'], dict):
			issue = payload['issue']
			if 'number' not in issue and 'number' in payload:
				issue['number'] = payload['number']
			if 'node_id' not in issue and 'id' in issue:
				issue['node_id'] = id2githubid('05:Issue', issue['id'])
		if 'comment' in payload and isinstance(payload['comment'], dict):
			comment = payload['comment']
			if 'node_id' not in comment and 'id' in comment and 'issue' in payload:
				comment['node_id'] = id2githubid('012:IssueComment', comment['id'])
			
		return payload
		
events = EventsDir(args.dir)

# first import users so they can be referenced in bugs
# this just scrapes the actor field for users right now.  add more fields as needed.

def importusers(events):
	with tqdm(total=events.filecount,desc='json files, new user details',unit='file') as progress:
		for event in events:
			actor = event['actor']
			login = actor['login']
			if login not in usermap:
				if 'name' in actor and 'email' in actor:
					usermap[login] = User(
						login,
						actor['name'],
						actor['email'],
						actor['avatar_url'],
						event['json'],
						actor['node_id']
					)
			progress.update(event['fileprogress'] - progress.n)
	with tqdm(total=events.filecount,desc='json files, new user summaries',unit='file') as progress:
		for event in events:
			actor = event['actor']
			login = actor['login']
			if login not in usermap and len(actor) > 1:
				if 'name' not in actor:
					actor['name'] = ''
				if 'email' not in actor:
					actor['email'] = ''
				usermap[login] = User(
					login,
					actor['name'],
					actor['email'],
					actor['avatar_url'],
					event['json'],
					actor['node_id']
				)
			progress.update(event['fileprogress'] - progress.n)

def importevent(event, events):
	created_at_timestamp = int(event['created_at_datetime'].timestamp())
	payload = event['payload']
	if 'issue' in payload:
		ghbug = payload['issue']
		number = ghbug['number']
		ghbug_new = number not in bugmap
		if ghbug_new:
			# this creates the bug if not created
			raise Exception("we're passing open/closed state but it's not being used yet.  probably only want to pass it if it's not being set the same; might make sense to set it afterwards instead of passing it, doesn't really matter.")
			bug = Bug(
				ghbug['title'],
				ghbug['body'],
				usermap[ghbug['user']['login']].hash,
				int(parsedate(ghbug['created_at']).timestamp()),
				'open',
				ghbug['url'],
				ghevent['json'],
				ghbug['node_id']
			)
			bugmap[number] = bug
			if ghbug['state'] == 'open':
				pass
			elif ghbug['state'] == 'closed':
				if payload['action'] != 'closed':
					bugmap.setstatus(bug, int(parsedate(ghbug['closed_at']).timestamp()), 'closed', ghevent['json'])
			else:
				raise Exception("not sure what state " + ghbug['state'] + " is")
		else:
			bug = bugmap[number]
	if event['type'] == 'IssuesEvent':
		# open and close are different from create
		if payload['action'] == 'opened' or payload['action'] == 'closed':
			state = {'opened':'open','closed':'closed'}[payload['action']]
			change = {'opened':'open','closed':'close'}[payload['action']]
			if created_at_timestamp < bug.modtime or bug.status == state:
				return
			usermap.adopt(event['actor']['login'])
			bugmap.setstatus(bug, created_at_timestamp, change, event['json'])
	elif event['type'] == 'IssueCommentEvent':
		# add comment to issue
		#if event[
		pass
	raise Exception(event)
		
	
try:
	with tqdm(total=events.filecount,desc='json files, events',unit='file') as progress:
		for event in events:
			importevent(event, events)
			progress.update(event['fileprogress'] - progress.n)
except Exception as e:
	event=e.args[0]
	del event['json']
	print(event)
importusers(events)
sys.exit(0)

# args.dir
numbers = [int(filename.split('.')[0]) for filename in os.listdir(args.dir)]
numbers.sort()

for event in events:
	if 'id' in event:
		del event['id']
	if 'url' in event:
		del event['url']
	del event['public']

	actor = event['actor']
	payload = event['payload']

	login = actor['login']
	if login in usermap:
		user = usermap[login]
	else:
		user = User(
			'@' + actor['login'] + ' ' + actor['name'],
			actor['email'],
			'https://gravatar.com/avatar/' + actor['gravatar_id'],
			actor
		)
		usermap[login] = user
	processed = False
	if event['type'] == 'IssuesEvent':
		if 'number' in payload:
			num = payload['number']
		else:
			num = payload['issue']['number']
			
		if num in bugmap:
			issue = bugmap[num]
		else:
			issue = None
			for event in events:
				subpayload = event['payload']
				if isinstance(subpayload['issue'], dict):
					issue = subpayload['issue']
					issue = Bug(
						issue['title'],
						issue['body'],
						issue['user']['login'],
						parsedate(issue['created_at'])
					)
					break
			if issue is None:
				issue = Bug(
					'lost issue #' + str(num),
					'The body of this issue was not recovered\n' +
					'```' +
					'\n'.join((json.dumps(event) for event in events))
					+ '\n```',
					None,
					parsedate(event['created_at'])
				)
			run('git bug user adopt', usermap[issue.user].hash)
			issue.hash = run(
				'git bug add',
				'--title', issue.title,
				'--message', issue.body,
				'--time', int(issue.time.timestamp())
			)[-1].split(' ')[0]
			issuemap[num] = issue
	run('git bug user adopt', user.hash)
	if event['type'] == 'IssuesEvent':
		if payload['action'] == 'closed':
			run(
				'git bug status close',
				issue.hash,
				'-u', int(parsedate(event['created_at']).timestamp())
			)
			processed = True
					
		
	if not processed:
		for key in event:
			print(key, event[key])
