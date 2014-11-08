#!/usr/bin/python
# GrepBugs.com

import os
import sys
import getopt
import uuid
import urllib2
import json
import sqlite3 as lite
import datetime
from subprocess import call
import subprocess
import cgi

dbfile  = os.path.dirname(os.path.abspath(__file__)) + '/data/grepbugs.db'
jfile   = os.path.dirname(os.path.abspath(__file__)) + '/data/grepbugs.json'
cindex  = os.path.dirname(os.path.abspath(__file__)) + '/third-party/codesearch/cindex'
csearch = os.path.dirname(os.path.abspath(__file__)) + '/third-party/codesearch/csearch'
cloctxt = '/tmp/gb.cloc.' + str(os.getpid()) + '.txt'
clocsql = '/tmp/gb.cloc.' + str(os.getpid()) + '.sql'

def local_scan(srcdir, repo='none', account='local_scan', project='none'):
	"""
	Perform a scan of local files
	"""
	scan_id = str(uuid.uuid1())
	basedir = os.path.dirname(os.path.abspath(__file__)) + '/' + srcdir.rstrip('/')

	try:
		db  = lite.connect(dbfile)
		cur = db.cursor()

	except lite.Error, e:
		print 'Error connecting to db file'
		sys.exit(1)

	# get latest greps
	try:
		print 'retreiving rules...'
		url = 'https://grepbugs.com/json'
		f   = urllib2.urlopen(url)
		j   = f.read()

		with open(jfile, 'wb') as jsonfile:
			jsonfile.write(j)

	except urllib2.URLError as error:
		print 'Error retreiving grep rules ' + str(error)
		sys.exit(1)

	# clean database
	cur.execute("DROP TABLE IF EXISTS metadata;");
	cur.execute("DROP TABLE IF EXISTS t;");

	# update database with new project info
	if 'none' == project:
		project = srcdir

	params     = [repo, account, project]
	newproject = True
	# query database
	cur.execute("SELECT project_id FROM projects WHERE repo=? AND account=? AND project=? LIMIT 1;", params)
	rows = cur.fetchall()

	for row in rows:
		newproject = False
		project_id = row[0]

	if True == newproject:
		project_id = str(uuid.uuid1())
		params     = [project_id, repo, account, project]
		cur.execute("INSERT INTO projects (project_id, repo, account, project) VALUES (?, ?, ?, ?);", params)

	# update databse with new scan info
	params  = [scan_id, project_id]
	cur.execute("INSERT INTO scans (scan_id, project_id) VALUES (?, ?);", params)
	db.commit()

	# execute cloc
	print 'counting source files...'
	call(["cloc", "--sql=" + clocsql, "--sql-project=" + srcdir, srcdir])
	# run sql script
	try:
		f = open(clocsql, 'r')
		cur.executescript(f.read())
		f.close
	except:
		print 'Error opening cloc sql file, perhaps no files in project.'
		return scan_id

	# execute clock again
	call(["cloc", "--quiet", "-out=" + cloctxt, srcdir])

	# save output
	f = open(cloctxt, 'r')
	params = [f.read(), scan_id]
	f.close
	cur.execute("UPDATE scans SET cloc_out=? WHERE scan_id=?;", params)


	# execute cindex
	print 'indexing source files...'
	call([cindex, "-reset"])
	call([cindex, srcdir])

	# load json data
	json_file = open(jfile, "r")
	data      = json.load(json_file)
	json_file.close()

	# query database
	cur.execute("SELECT DISTINCT Language FROM t;")
	rows = cur.fetchall()

	# grep all the bugs and output to file
	print 'grepping for bugs...'

	for row in rows:
		count = 0
		for i in range(0, len(data)):
				if row[0] == data[i]["language"]:
					ext        = ''
					extension  = ''
					proc       = subprocess.Popen(["cloc", "--show-ext"], stdout=subprocess.PIPE)
					ext        = proc.communicate()
					extarray   = str(ext[0]).split("\n")
					extensions = []

					for ii in range(0, len(extarray)):
						lang = str(extarray[ii]).split("->")
						if len(lang) > 1:
							if str(lang[1]).strip() == data[i]["language"]:
								extensions.append(str(lang[0]).strip())

					result = ''
					filter = ".*(" + "|".join(extensions) + ")$"
					proc   = subprocess.Popen([csearch, "-i", "-f", filter, data[i]["regex"]], stdout=subprocess.PIPE)
					result = proc.communicate()

					if len(result[0]):
						# update databse with new results info
						result_id = str(uuid.uuid1())
						params    = [result_id, scan_id, data[i]["language"], data[i]["id"], data[i]["regex"], data[i]["description"]]
						cur.execute("INSERT INTO results (result_id, scan_id, language, regex_id, regex_text, description) VALUES (?, ?, ?, ?, ?, ?);", params)

						db.commit()

						perline = str(result[0]).split("\n")
						for r in range(0, len(perline) - 1):
							rr = str(perline[r]).replace(basedir, '').split(':', 1)
							# update databse with new results_detail info
							result_detail_id = str(uuid.uuid1())
							params           = [result_detail_id, result_id, rr[0], str(rr[1]).strip()]

							try:
								cur.execute("INSERT INTO results_detail (result_detail_id, result_id, file, code) VALUES (?, ?, ?, ?);", params)
							except lite.Error, e:
								print params
								print e
								# should log this or something

							db.commit()

	params = [project_id]
	cur.execute("UPDATE projects SET last_scan=datetime('now') WHERE project_id=?;", params)
	db.commit()
	db.close()

	html_report(scan_id)

	return scan_id

def repo_scan(repo, account):
	"""
	Check code out from a remote repo and scan import
	"""
	try:
		db  = lite.connect(dbfile)
		cur = db.cursor()

	except lite.Error, e:
		print 'Error connecting to db file'
		sys.exit(1)

	params = [repo]
	cur.execute("SELECT command, checkout_url, api_url FROM repo_sites WHERE site=? LIMIT 1;", params)
	rows = cur.fetchall()

	for row in rows:
		co_url  = row[1].replace('ACCOUNT', account)
		api_url = row[2].replace('ACCOUNT', account)

		# call api_url
		data = json.load(urllib2.urlopen(api_url))

		if 'github' == repo:
			for i in range(0, len(data)):
				do_scan      = True
				project_name = data[i]["name"]
				last_scanned = last_scan(repo, account, project_name)
				last_changed = datetime.datetime.strptime(data[i]['pushed_at'], "%Y-%m-%dT%H:%M:%SZ")
				checkout_url = 'https://github.com/' + account + '/' + project_name + '.git'
				cmd          = 'git'

				print project_name + ' last changed on ' + str(last_changed) + ' and last scanned on ' + str(last_scanned)

				if None != last_scanned:
					if last_changed < last_scanned:
						do_scan = False

				if True == do_scan:
					checkout_code(cmd, checkout_url, account, project_name)
					# scan local files
					local_scan(os.path.dirname(os.path.abspath(__file__)) + '/src/' + account + '/' + project_name, repo, account, project_name)

		elif 'bitbucket' == repo:
			for j in range(0, len(data["values"])):
				value =  data["values"][j]

				if 'git' == value['scm']:
					do_scan      = True
					project_name = str(value['full_name']).split('/')[1]
					last_scanned = last_scan(repo, account, project_name)
					date_split   = str(value['updated_on']).split('.')[0]
					last_changed = datetime.datetime.strptime(date_split, "%Y-%m-%dT%H:%M:%S")
					checkout_url = 'https://bitbucket.org/' + value['full_name']
					cmd          = 'git'

					print project_name + ' last changed on ' + str(last_changed) + ' and last scanned on ' + str(last_scanned)

					if None != last_scanned:
						if last_changed < last_scanned:
							do_scan = False

					if True == do_scan:
						checkout_code(cmd, checkout_url, account, project_name)
						# scan local files
						local_scan(os.path.dirname(os.path.abspath(__file__)) + '/src/' + account + '/' + project_name, repo, account, project_name)

		elif 'sourceforge' == repo:
			for i in data['projects']:
				do_scan      = True
				project_name = i["url"].replace('/p/', '').replace('/', '')
				cmd          = None 
				project_json = json.load(urllib2.urlopen('https://sourceforge.net/rest' + i['url']))
				for j in project_json:
					for t in project_json['tools']:
						if 'code' == t['mount_point']:
							if 'git' == t['name']:
								cmd          = 'git'
								checkout_url = 'git://git.code.sf.net/p/' + str(project_name).lower() + '/code'
							elif 'svn' == t['name']:
								cmd          = 'svn'
								checkout_url = 'svn://svn.code.sf.net/p/' + str(project_name).lower() + '/code'

				last_scanned = last_scan(repo, account, project_name)
				date_split   = i['last_updated'].split('.')[0]
				last_changed = datetime.datetime.strptime(date_split, "%Y-%m-%d %H:%M:%S")

				print project_name + ' last changed on ' + str(last_changed) + ' and last scanned on ' + str(last_scanned)

				if None != last_scanned:
					if last_changed < last_scanned:
						do_scan = False

				if True == do_scan:
					if None != cmd:
						checkout_code(cmd, checkout_url, account, project_name)
						# scan local files
						local_scan(os.path.dirname(os.path.abspath(__file__)) + '/src/' + account + '/' + project_name, repo, account, project_name)
					else:
						print 'No sourceforge repo for ' + account + ' ' + project_name

		db.close()
		print 'SCAN COMPLETE!'

def checkout_code(cmd, checkout_url, account, project):
	account_folder = os.path.dirname(os.path.abspath(__file__)) + '/src/' + account

	if not os.path.exists(account_folder):
		os.makedirs(account_folder)

	# checkout code
	call(['rm', '-rf', account_folder + '/' + project])
	if 'git' == cmd:
		print 'git clone...'
		call(['git', 'clone', checkout_url, account_folder + '/' + project])
	elif 'svn' == cmd:
		# need to do a lot of craziness for svn, no wonder people use git now.
		print 'svn checkout...'
		found_trunk = False

		call(['svn', '-q', 'checkout', '--depth', 'immediates', checkout_url, account_folder + '/tmp/' + project])

		# look for first level trunks
		for path, dirs, files in os.walk(os.path.abspath(account_folder + '/tmp/' + project)):
			for i in range(0, len(dirs)):
				if 'trunk' == dirs[i]:
					if os.path.isdir(path + '/' + dirs[i]):
						found_trunk = True
						print 'co ' + checkout_url + '/' + dirs[i]
						call(['svn', '-q', 'checkout', checkout_url + '/' + dirs[i], account_folder + '/' + project])

		if False == found_trunk:
			# try looking for tunk in second level
			path = os.path.abspath(account_folder + '/tmp/' + project)
			for n in os.listdir(path):
				if os.path.isdir(path + '/' + n):
					if '.svn' != n:
						print 'co ' + checkout_url + '/' + n + '/trunk'
						return_code = call(['svn', '-q', 'checkout', checkout_url + '/' + n + '/trunk', account_folder + '/' + project])
						if 0 == return_code:
							found_trunk = True

		if False == found_trunk:
			# didn't find a trunk, so checkout of last resort
			print 'WARNING: no trunk found so checking out everything. This could take a while and consume disk space if there are many branches.'
			call(['svn', '-q', 'checkout', checkout_url, account_folder + '/' + project])

		# remove temp checkout
		call(['rm', '-rf', os.path.abspath(account_folder + '/tmp/')])


def last_scan(repo, account, project):
	try:
		db  = lite.connect(dbfile)
		cur = db.cursor()

	except lite.Error, e:
		print 'Error connecting to db file'
		sys.exit(1)

	params = [repo, account, project]
	cur.execute("SELECT last_scan FROM projects WHERE repo=? AND account=? and project=?;", params)
	rows      = cur.fetchall()
	last_scan = None
	
	for row in rows:
		if None != row[0]:
			last_scan = datetime.datetime.strptime(str(row[0]), "%Y-%m-%d %H:%M:%S")

	db.close()
	return last_scan

def html_report(scan_id):
	"""
	Create html report for a given scan_id
	"""
	try:
		db  = lite.connect(dbfile)
		cur = db.cursor()

	except lite.Error, e:
		print 'Error connecting to db file'
		sys.exit(1)

	html   = ''
	h      = 'ICAgX19fX19fICAgICAgICAgICAgICAgIF9fX18KICAvIF9fX18vX19fX19fXyAgX19fXyAgLyBfXyApX18gIF9fX19fXyBfX19fX18KIC8gLyBfXy8gX19fLyBfIFwvIF9fIFwvIF9fICAvIC8gLyAvIF9fIGAvIF9fXy8KLyAvXy8gLyAvICAvICBfXy8gL18vIC8gL18vIC8gL18vIC8gL18vIChfXyAgKQpcX19fXy9fLyAgIFxfX18vIC5fX18vX19fX18vXF9fLF8vXF9fLCAvX19fXy8KICAgICAgICAgICAgICAvXy8gICAgICAgICAgICAgICAgL19fX18v'
	params = [scan_id]

	cur.execute("SELECT a.repo, a.account, a.project, b.scan_id, b.date_time, b.cloc_out FROM projects a, scans b WHERE a.project_id=b.project_id AND b.scan_id=? LIMIT 1;", params)
	rows = cur.fetchall()

	for row in rows:
		print 'writing report...'
		htmlfile = os.path.dirname(os.path.abspath(__file__)) + '/out/' + row[0] + '.' + row[1] + '.' + row[2].replace("/", "_") + '.' + row[3] + '.html'

		o = open(htmlfile, 'w')
		o.write("<!DOCTYPE html><pre>\n" + h.decode('base64') + "</pre>")
		o.write("<pre>\n" + "repo: " + row[0] + "\naccount: " + row[1] + "\nproject: " + row[2] + "\nscan id: " + row[3] + "\ndate: " + row[4] + "</pre>\n")
		o.write("<pre>\n" + str(row[5]).replace("\n", "<br>") + "</pre>")
		o.close()

		cur.execute("SELECT b.language, b.regex_text, b.description, c.result_detail_id, c.file, c.code FROM scans a, results b, results_detail c WHERE a.scan_id=? AND a.scan_id=b.scan_id AND b.result_id=c.result_id ORDER BY b.language, b.regex_id, c.file;", params)
		rs       = cur.fetchall()
		o        = open(htmlfile, 'a')
		html     = ''
		language = ''
		regex    = ''
		count    = 0

		for r in rs:
			if 0 == count:
				html += '<div>'

			if language != r[0]:
				html += '<h4>' + r[0] + '</h4>' + "\n"

			if regex != r[1]:
				if 0 != count:
					html += '</div>'
					html += '</div>'

				html += '<div style="margin-left:15px;">'
				html += '<a name="' + str(r[3]) + '">' + "\n"
				html += '<a style="cursor: pointer;" onclick="javascript:o=document.getElementById(\'r' + str(r[3]) + '\');if(o.style.display==\'none\'){ o.style.display=\'block\';} else {o.style.display=\'none\';}">+</a> ' + r[2] + "\n"
				html += '<div id="r' + str(r[3]) + '" style="display:none;">' + "\n"
				html += '<div style="font-weight:bold;"><pre>' +  cgi.escape(r[1]) + '</pre></div>'
				html += '<pre style="margin-left:50px;"><span style="color:gray;">' + r[4] + ':</span> &nbsp; ' + cgi.escape(r[5]) + '</pre>' + "\n"

			else:
				html += '<pre style="margin-left:50px;"><span style="color:gray;">' + r[4] + ':</span> &nbsp; ' + cgi.escape(r[5]) + '</pre>' + "\n"

			count   += 1
			language = r[0]
			regex    = r[1]

		html += '</div>'
		html += '</div>'
		html += '</div>'
		html += '</html>'
		o.write(html)
		o.close()
	db.close()

def argue(argv):
	"""
	Handle command line arguments
	"""
	try:
		noarg = True
		opts, args = getopt.getopt(argv, "hd:r:a:c")

	except getopt.GetoptError as error:
		print 'exception: python grepbugs.py ' + str(error)
		os._exit(2)

	for opt, arg in opts:
		noarg = False

		if '-h' == opt:
			print 'usage'
			os._exit(0)
		elif '-d' == opt:
				print 'scan directory: ' + arg
				scan_id = local_scan(arg)
		elif '-r' == opt:
				a_exists = False
				for opt, account in opts:
					if '-a' == opt:
						a_exists = True
						print 'scan repo: ' + arg + ' ' + account
						scan_id = repo_scan(arg, account)

				if False == a_exists:
					usage()
		elif '-c' == opt:
			usage()

	if noarg == True:
			usage()

def usage():
	print "python grepbugs.py [options]"
	print "\t-d\tScan a directory, specify a directory. -d src/example"
	print "\t-r\tScan a repository, spefcify repository (github). Use with -a option."
	print "\t-a\tRepository account to be scanned. Use with -r option."

argue(sys.argv[1:])
