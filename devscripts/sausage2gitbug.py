#!/usr/bin/env python3

import argparse
from dataclasses import dataclass
import dateutil.parser
import json
import os
import subprocess

print('\npass the path to the issues folder containing a bunch of json as the first argument\n')

print('this uses xloem\'s fork of git-bug that lets manual timestamps be supplied')
print('https://github.com/MichaelMure/git-bug/pull/492\n')

# NEXT: load all users first, since they can be referred to before their details are available

def run(cmd, *args, replies = None):
	proc = subprocess.Popen([*cmd.split(' '), *(str(arg) for arg in args)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
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
	retcode = proc.wait()
	if retcode:
		print(proc.stdout.read())
		print(proc.stderr.read())
		raise subprocess.CalledProcessError(retcode, proc.args)
	return proc.stdout.read().decode().strip().split('\n')

@dataclass
class User:
	name : str
	email : str
	avatar : str
	json : dict
	hash : str = ''
	def __init__(self, name : str, email : str, avatar : str, json : dict):
		self.name = name
		self.email = email
		self.avatar = avatar
		self.json = json
		self.hash = run(
			'git bug user create',
			replies = {
				'Name: ': self.name,
				'Email: ': self.email,
				'Avatar URL: ': self.avatar
			}
		)[-1]

@dataclass
class Issue:
	title : str
	body : str
	user : str
	time : int
	hash : str = ''

usermap = {
	None: User('Unknown User', 'unknown@localhost.localdomain', '', 1)
}
issuemap = {}
eventmap = {}


parser = argparse.ArgumentParser()
parser.add_argument("dir", help="path to the json issues")
args = parser.parse_args()

def parsedate(item):
	return dateutil.parser.isoparse(item.replace('/','-').replace(' -','-').replace(' +','+'))

# args.dir
numbers = [int(filename.split('.')[0]) for filename in os.listdir(args.dir)]
numbers.sort()

if len(numbers):
	print('WARNING: this is just a work in progress and will make new issues and users in your git-bug repository every time it is run.')
	input('continue?')

filenames = (str(number) + '.json' for number in numbers)
for filename in filenames:
	print('\n',filename)
	filename = os.path.join(args.dir, filename)
	with open(filename) as file:
		events = [json.loads(line) for line in file.readlines()]
	
	for event in events:
		if 'actor_attributes' in event:
			event['actor'] = event['actor_attributes']
			del event['actor_attributes']
		if 'repo' in event:
			del event['repo']
		if 'repository' in event:
			del event['repository']
		if 'id' in event:
			del event['id']
		if 'url' in event:
			del event['url']
		del event['public']
	events.sort(key = lambda event: parsedate(event['created_at']))
	for event in events:
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
				
			if num in issuemap:
				issue = issuemap[num]
			else:
				issue = None
				for event in events:
					subpayload = event['payload']
					if isinstance(subpayload['issue'], dict):
						issue = subpayload['issue']
						issue = Issue(
							issue['title'],
							issue['body'],
							issue['user']['login'],
							parsedate(issue['created_at'])
						)
						break
				if issue is None:
					issue = Issue(
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
	
	#break
