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
import time
import logging

dbfile  = os.path.dirname(os.path.abspath(__file__)) + '/data/grepbugs.db'
gbfile   = os.path.dirname(os.path.abspath(__file__)) + '/data/grepbugs.json'
cindex  = os.path.dirname(os.path.abspath(__file__)) + '/third-party/codesearch/cindex'
csearch = os.path.dirname(os.path.abspath(__file__)) + '/third-party/codesearch/csearch'
logfile = os.path.dirname(os.path.abspath(__file__)) + '/log/grepbugs.log'

# setup logging; create directory if it doesn't exist, and configure logging
if not os.path.exists(os.path.dirname(logfile)):
	os.makedirs(os.path.dirname(logfile))

logging.basicConfig(filename=logfile, level=logging.DEBUG, format='%(asctime)s %(levelname)s %(message)s', datefmt='%m/%d/%Y %I:%M:%S %p')

def local_scan(srcdir, repo='none', account='local_scan', project='none'):
	"""
	Perform a scan of local files
	"""
	# new scan so new scan_id
	scan_id = str(uuid.uuid1())
	cloctxt = '/tmp/gb.cloc.' + scan_id + '.txt'
	clocsql = '/tmp/gb.cloc.' + scan_id + '.sql'
	basedir = os.path.dirname(os.path.abspath(__file__)) + '/' + srcdir.rstrip('/')
	logging.info('Starting local scan with scsan id ' + scan_id)

	# get db connection
	try:
		db  = lite.connect(dbfile)
		cur = db.cursor()

	except lite.Error as e:
		print 'Error connecting to db file! See log file for details.'
		logging.debug('Error connecting to db file: ' + str(e))
		sys.exit(1)
	except Exception as e:
		print 'CRITICAL: Unhandled exception occured! Quiters gonna quit! See log file for details.'
		logging.critical('Unhandled exception: ' + str(e))
		sys.exit(1)

	# get latest greps
	try:
		url = 'https://grepbugs.com/json'
		print 'retreiving rules...'
		logging.info('Retreiving rules from ' + url)

		# if request fails, try 3 times
		count     = 0
		max_tries = 3
		while count < max_tries:
			try:
				f   = urllib2.urlopen(url)
				j   = f.read()

				with open(gbfile, 'wb') as jsonfile:
					jsonfile.write(j)

				# no exceptions so break out of while loop
				break
			except urllib2.URLError as e:
				count = count + 1
				if count <= max_tries:
					logging.warning('Error retreiving grep rules (attempt ' + str(count) + ' of ' + str(max_tries) + '): ' + str(e))
					time.sleep(3)

			except Exception as e:
				print 'CRITICAL: Unhandled exception occured! Quiters gonna quit! See log file for details.'
				logging.critical('Unhandled exception: ' + str(e))
				sys.exit(1)

		if count == max_tries:
			# grep rules were not retrieved, could be working with old rules.
			logging.debug('Error retreiving grep rules (no more tries left. could be using old grep rules.): ' + str(e))
				
	except Exception as e:
		print 'CRITICAL: Unhandled exception occured! Quiters gonna quit! See log file for details.'
		logging.critical('Unhandled exception: ' + str(e))
		sys.exit(1)

	# prep db for capturing scan results
	try:
		# clean database
		cur.execute("DROP TABLE IF EXISTS metadata;");
		cur.execute("DROP TABLE IF EXISTS t;");
		cur.execute("VACUUM");

		# update database with new project info
		if 'none' == project:
			project = srcdir

		# query database
		params     = [repo, account, project]
		cur.execute("SELECT project_id FROM projects WHERE repo=? AND account=? AND project=? LIMIT 1;", params)
		rows = cur.fetchall()

		# assume new project by default
		newproject = True

		for row in rows:
			# not so fast, not a new project
			newproject = False
			project_id = row[0]

		if True == newproject:
			project_id = str(uuid.uuid1())
			params     = [project_id, repo, account, project]
			cur.execute("INSERT INTO projects (project_id, repo, account, project) VALUES (?, ?, ?, ?);", params)

		# update database with new scan info
		params  = [scan_id, project_id]
		cur.execute("INSERT INTO scans (scan_id, project_id) VALUES (?, ?);", params)
		db.commit()
	except lite.Error as e:
		print 'Error prepping database! See log file for details.'
		logging.debug('Error with database: ' + str(e))
		sys.exit(1)
	except Exception as e:
		print 'CRITICAL: Unhandled exception occured! Quiters gonna quit! See log file for details.'
		logging.critical('Unhandled exception: ' + str(e))
		sys.exit(1)

	# execute cloc to get sql output
	try:
		print 'counting source files...'
		logging.info('Running cloc for sql output.')
		return_code = call(["cloc", "--skip-uniqueness", "--quiet", "--sql=" + clocsql, "--sql-project=" + srcdir, srcdir])
		if 0 != return_code:
			raise ClocSQLFail('WARNING: cloc did not run normally. return code: ' + str(return_code))
		
		# run sql script generated by cloc to save output to database
		f = open(clocsql, 'r')
		cur.executescript(f.read())
		db.commit()
		f.close
		os.remove(clocsql)
	except ClocSQLFail as e:
		print e
		logging.debug(e)
	except Exception as e:
		print 'Error executing cloc sql! Aborting scan! See log file for details.'
		loggin.debug('Error executing cloc sql (scan aborted): ' + str(e))
		return scan_id

	# execute clock again to get txt output
	try:
		logging.info('Running cloc for txt output.')
		call(["cloc", "--skip-uniqueness", "--quiet", "-out=" + cloctxt, srcdir])

		# save cloc txt output to database
		f = open(cloctxt, 'r')
		params = [f.read(), scan_id]
		f.close
		cur.execute("UPDATE scans SET cloc_out=? WHERE scan_id=?;", params)
		db.commit()
		os.remove(cloctxt)
	except Exception as e:
		print 'Error saving cloc txt! Aborting scan! See log file for details.'
		loggin.debug('Error saving cloc txt (scan aborted): ' + str(e))
		return scan_id

	# execute cindex
	try:
		print 'indexing source files...'
		logging.info('Indexing source files.')
		call([cindex, "-reset"])
		call([cindex, srcdir])
	except Exception as e:
		print 'CRITICAL: Unhandled exception occured! Quiters gonna quit! See log file for details.'
		logging.critical('Unhandled exception: ' + str(e))
		sys.exit(1)

	# load json data
	try:
		logging.info('Reading grep rules from json file.')
		json_file = open(gbfile, "r")
		greps     = json.load(json_file)
		json_file.close()
	except Exception as e:
		print 'CRITICAL: Unhandled exception occured! Quiters gonna quit! See log file for details.'
		logging.critical('Unhandled exception: ' + str(e))
		sys.exit(1)

	# query database
	cur.execute("SELECT DISTINCT Language FROM t ORDER BY Language;")
	rows = cur.fetchall()

	# grep all the bugs and output to file
	print 'grepping for bugs...'
	logging.info('Start grepping for bugs.')

	# get cloc extensions and create extension array
	clocext  = ''
	proc     = subprocess.Popen(["cloc", "--show-ext"], stdout=subprocess.PIPE)
	ext      = proc.communicate()
	extarray = str(ext[0]).split("\n")
	
	# override some extensions
	extarray.append('inc -> PHP')
	
	# loop through languages identified by cloc
	for row in rows:
		count = 0
		# loop through all grep rules for each language identified by cloc
		for i in range(0, len(greps)):
				# if the language matches a language in the gb rules file then do stuff
				if row[0] == greps[i]['language']:

					# get all applicable extensions based on language
					extensions = []
					for ii in range(0, len(extarray)):
						lang = str(extarray[ii]).split("->")
						if len(lang) > 1:							
							if str(lang[1]).strip() == greps[i]['language']:
								extensions.append(str(lang[0]).strip())

					# search with regex, filter by extensions, and capture result
					result = ''
					filter = ".*\.(" + "|".join(extensions) + ")$"
					try:
						proc   = subprocess.Popen([csearch, "-i", "-f", filter, greps[i]['regex']], stdout=subprocess.PIPE)
						result = proc.communicate()

						if len(result[0]):
							# update database with new results info
							result_id = str(uuid.uuid1())
							params    = [result_id, scan_id, greps[i]['language'], greps[i]['id'], greps[i]['regex'], greps[i]['description']]
							cur.execute("INSERT INTO results (result_id, scan_id, language, regex_id, regex_text, description) VALUES (?, ?, ?, ?, ?, ?);", params)
							db.commit()

							perline = str(result[0]).split("\n")
							for r in range(0, len(perline) - 1):
								try:
									rr = str(perline[r]).replace(basedir, '').split(':', 1)
									# update database with new results_detail info
									result_detail_id = str(uuid.uuid1())
									params           = [result_detail_id, result_id, rr[0], str(rr[1]).strip()]

									cur.execute("INSERT INTO results_detail (result_detail_id, result_id, file, code) VALUES (?, ?, ?, ?);", params)
								except lite.Error, e:
									print 'SQL error! See log file for details.'
									logging.debug('SQL error with params ' + str(params) + ' and error ' + str(e))
								except Exception as e:
									print 'Error parsing result: ' + str(perline[r])
									logging.debug('Error parsing result: ' + sr(e))

								db.commit()

					except Exception as e:
						print 'Error calling csearch! See log file for details'
						logging.debug('Error calling csearch: ' + sr(e))

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

	except lite.Error as e:
		print 'Error connecting to db file'
		logging.debug('Error connecting to db file' + str(e))
		sys.exit(1)

	params = [repo]
	cur.execute("SELECT command, checkout_url, api_url FROM repo_sites WHERE site=? LIMIT 1;", params)
	rows = cur.fetchall()

	for row in rows:
		api_url = row[2].replace('ACCOUNT', account)

		if 'github' == repo:
			page = 1
			
			# call api_url
			data = json.load(urllib2.urlopen(api_url + '?page=' + str(page) + '&per_page=100'))
			
			while len(data):
				print 'Get page: ' + str(page)
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
						# clean up because of big projects and stuff
						call(['rm', '-rf', os.path.dirname(os.path.abspath(__file__)) + '/src/' + account + '/' + project_name])
						
				
				page += 1
				data = json.load(urllib2.urlopen(api_url + '?page=' + str(page) + '&per_page=100')) # get next page of projects

		elif 'bitbucket' == repo:
			# call api_url
			data = json.load(urllib2.urlopen(api_url))
			
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
			# call api_url
			data = json.load(urllib2.urlopen(api_url))
			
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

	# for loop on rows, but only one row
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
		html     = "\n\n"
		language = ''
		regex    = ''
		count    = 0

		# loop through all results, do some fancy coordination for output
		for r in rs:
			if regex != r[1]:
				if 0 != count:
					html += '		</div></div>'; # end result set for regex

			if language != r[0]:
				if 0 != count:
					html += '		</div>'; # end result set for language
				html += '<h4>' + r[0] + '</h4>' + "\n"

			if regex != r[1]:
				html += '<div style="margin-left:15px;">' + "\n"
				html += '	<a name="' + str(r[3]) + '">' + "\n"
				html += '	<a style="cursor: pointer;" onclick="javascript:o=document.getElementById(\'r' + str(r[3]) + '\');if(o.style.display==\'none\'){ o.style.display=\'block\';} else {o.style.display=\'none\';}">+ ' + r[2] + "</a>\n"
				html += '	<div id="r' + str(r[3]) + '" style="display:none;">' + "\n" # description
				html += '		<div style="font-weight:bold;"><pre>' +  cgi.escape(r[1]) + '</pre></div>' #regex

			html += '		<pre style="margin-left:50px;"><span style="color:gray;">' + r[4] + ':</span> &nbsp; ' + cgi.escape(r[5]) + '</pre>' + "\n" # finding

			count   += 1
			language = r[0]
			regex    = r[1]

		if 0 == count:
			html += '<h3>No bugs found!</h3><div>Contirbute regular expressions to find bugs in this code at <a href="https://grepbugs.com">GrepBugs.com</a></div>';
		else:
			html += '</div></div></div>'
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
