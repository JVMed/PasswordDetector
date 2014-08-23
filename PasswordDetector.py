#!/usr/bin/env python
#
# Queue-script for NZBGet
#
# Copyright (C) 2014 Andrey Prygunkov <hugbug@users.sourceforge.net>
# Copyright (C) 2014 Clinton Hall <clintonhall@users.sourceforge.net>
# Copyright (C) 2014 JVM <jvmed@users.sourceforge.net>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
# 
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
# 
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301, USA.
#

##############################################################################
### NZBGET QUEUE/POST-PROCESSING SCRIPT                                    ###

# Detect nzbs with password protected .rar archive.
#
# If a password is detected the download is marked as bad or paused. This status
# informs other scripts about failure and allows NZBGet to choose
# another duplicate for download (if available).
#
# Further discussion at: http://nzbget.net/forum/viewtopic.php?f=8&t=1391
#
#
# PP-Script version: 1.1.
#
# NOTE: Requires NZBGet v14 r1093+
#
#
# NOTE: For best results select this script in both options: QueueScript
# and PostScript, preferably as the first script. This ensures earlier
# detection.
#
# NOTE: This script requires Python to be installed on your system (tested
# only with Python 2.x; may not work with Python 3.x).
#
##############################################################################
### OPTIONS                                                                ###

# Comma separated list of categories to detect passwords for.
#
# If the list is empty all downloads (from all categories) are checked.
# Wildcards (* and ?) are supported, for example: *tv*,*movie*.
#Categories=*tv*,*movie*

# Action if password found (Pause, Mark Bad).
#
# Pause allows to define password. Once resumed, password detection skipped to 
# allow NZB to complete.
#
# Mark bad removes the download from queue and (if option "DeleteCleanupDisk" is active) the
# downloaded files are deleted from disk. If duplicate handling is active
# (option "DupeCheck") then another duplicate is chosen for download
# if available.
#
#PassAction=Pause

# Sort Inner files (Enable, Disable).
#
# Disable if also using FakeDetector to avoid sorting twice.
#
#SortInner=Enable

### NZBGET QUEUE/POST-PROCESSING SCRIPT                                    ###
##############################################################################

import os
import sys
import subprocess
import fnmatch
import re
import glob
import urllib2
import base64

from xmlrpclib import ServerProxy

# Exit codes used by NZBGet
POSTPROCESS_SUCCESS=93
POSTPROCESS_NONE=95
POSTPROCESS_ERROR=94

verbose = False # Verbose output of unrar for debugging

# Start up checks
def start_check():
	# Check if the script is called from a compatible NZBGet version (as queue-script or as pp-script)
	if not ('NZBNA_EVENT' in os.environ or 'NZBPP_DIRECTORY' in os.environ) or not 'NZBOP_ARTICLECACHE' in os.environ:
		print('*** NZBGet queue script ***')
		print('This script is supposed to be called from nzbget (14.0 or later).')
		sys.exit(1)
	
	# This script processes only certain queue events.
	# For compatibility with newer NZBGet versions it ignores event types it doesn't know
	if os.environ.get('NZBNA_EVENT') not in ['NZB_ADDED', 'FILE_DOWNLOADED', 'NZB_DOWNLOADED', None]:
		print('[INFO] Not vaild queue event for script')
		sys.exit(0)
		
	# Remove temp file
	if 'NZBPP_DIRECTORY' in os.environ:
		clean_up()
		sys.exit(POSTPROCESS_SUCCESS)
		# If called via "Post-process again" from history details dialog the download may not exist anymore
		# if not os.path.exists(os.environ.get('NZBPP_DIRECTORY')):
			# print('Destination directory doesn\'t exist, exiting')
			# sys.exit(POSTPROCESS_NONE)
	
	# If nzb is already failed, don't do any further detection
	if os.environ.get('NZBPP_TOTALSTATUS') == 'FAILURE':
		sys.exit(POSTPROCESS_NONE)
		
	# Check if password previously found
	if os.environ.get('NZBPR_PASSWORDDETECTOR_HASPASSWORD')=='yes':
		print('[DETAIL] Password previously found, skipping detection')
		sys.exit(POSTPROCESS_SUCCESS)
		
	# When nzb is added to queue - reorder inner files for earlier fake detection
	if (os.environ['NZBPO_SORTINNER'] == 'Yes') and (os.environ.get('NZBNA_EVENT') == 'NZB_ADDED'):
		print('[INFO] Sorting inner files for earlier fake detection for %s' % NzbName)
		sys.stdout.flush()
		sort_inner_files()
		sys.exit(POSTPROCESS_NONE)
	
	# Check settings
	required_options = ('NZBPO_CATEGORIES', 'NZBPO_PASSACTION', 'NZBPO_SORTINNER')
	for	optname in required_options:
		if (not optname in os.environ):
			print('[ERROR] Option %s is missing in configuration file. Please check script settings' % optname[6:])
			sys.exit(POSTPROCESS_ERROR)
		
# If the option "CATEGORIES" is set - check if the nzb has a category from the list
def check_category(category):
	Categories = os.environ.get('NZBPO_CATEGORIES', '').split(',')
	if Categories == [] or Categories == ['']:
		return True
	
	for m_category in Categories:
		if m_category <> '' and fnmatch.fnmatch(category, m_category):
			return True
	return False

# Finds untested files, comparing all files and processed files in tmp_file
def get_latest_file(dir):
	try:
		with open(tmp_file_name) as tmp_file:
			tested = tmp_file.read().splitlines()
			if not os.path.exists(dir):
				print("[INFO] Intermediate download folder doesn't yet exist:  " + temp_folder)
				return
			files = os.listdir(dir)
			return list(set(files)-set(tested))
	except:
		# tmp_file doesn't exist, all files need testing
		temp_folder = os.path.dirname(tmp_file_name)
		if not os.path.exists(temp_folder):
			os.makedirs(temp_folder)
			print('[DETAIL] Created folder ' + temp_folder)
		with open(tmp_file_name, "w") as tmp_file:
			tmp_file.write('')
			print('[DETAIL] Created temp file ' + tmp_file_name)
		return ''

# Saves tested files so to not test again
def save_tested(data):
	with open(tmp_file_name, "a") as tmp_file:
		tmp_file.write(data)
		
# Checks files for passwords without unpacking
def contains_password(dir):
	files = get_latest_file(dir)
	tested = ''
	for file in files:
		# avoid .tmp files as corrupt
		if not "tmp" in file:
			try:
				command = [os.environ['NZBOP_UNRARCMD'], "l -p-",  dir + '/' + file]
				if verbose:
					print('command: %s' % command)
				proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
				out, err = proc.communicate()
				if verbose:
					print(out)
					print(err)
				if "*" in out:
					return True
				if err.find("wrong password") != -1:
					return True
			except:
				print('[ERROR] Something went wrong checking %s' % file) 
		tested += file + '\n'
	save_tested(tested)

def connectToNzbGet():
	global nzbget
	# First we need to know connection info: host, port and password of NZBGet server.
	# NZBGet passes all configuration options to post-processing script as
	# environment variables.
	host = os.environ['NZBOP_CONTROLIP']
	port = os.environ['NZBOP_CONTROLPORT']
	username = os.environ['NZBOP_CONTROLUSERNAME']
	password = os.environ['NZBOP_CONTROLPASSWORD']
	
	if host == '0.0.0.0': host = '127.0.0.1'
	
	# Build an URL for XML-RPC requests
	# TODO: encode username and password in URL-format
	rpcUrl = 'http://%s:%s@%s:%s/xmlrpc' % (username, password, host, port);
	
	# Create remote server object
	nzbget = ServerProxy(rpcUrl)

# Pause NZB group by API
def pause_nzb(NZBID):
	# Setup connection to NZBGet RPC-server
	connectToNzbGet()
	# Pause nzb
	nzbget.editqueue('GroupPause', 0, '', [int(NZBID)])

# Remove temp file in PP
def clean_up():
	try:
		if os.path.isfile(tmp_file_name):
			os.remove(tmp_file_name)
			print('[DETAIL] Completed removing temp file ' + tmp_file_name)
		else:
			print('[DETAIL] No temp file to remove: ' + tmp_file_name)
	except:
		print('[ERROR] Removing temp file was unsuccesful: ' + tmp_file_name)

# Reorder inner files for earlier fake detection
def sort_inner_files():
	nzb_id = int(os.environ.get('NZBNA_NZBID'))

	# Establish connection to NZBGet via RPC-API

	# First we need to know connection info: host, port and password of NZBGet server.
	# NZBGet passes all configuration options to scripts as environment variables.
	host = os.environ['NZBOP_CONTROLIP']
	if host == '0.0.0.0': host = '127.0.0.1'
	port = os.environ['NZBOP_CONTROLPORT']
	username = os.environ['NZBOP_CONTROLUSERNAME']
	password = os.environ['NZBOP_CONTROLPASSWORD']
	
	# Build an URL for XML-RPC requests
	# TODO: encode username and password in URL-format
	xmlRpcUrl = 'http://%s:%s@%s:%s/xmlrpc' % (username, password, host, port);
	
	# Create remote server object
	nzbget = ServerProxy(xmlRpcUrl)

	# Obtain the list of inner files belonging to this nzb using RPC-API method "listfiles".
	# For details see http://nzbget.net/RPC_API_reference

	# It's very easier to get the list of files from NZBGet using XML-RPC:
	#	queued_files = nzbget.listfiles(0, 0, nzb_id)
	
	# However for large file lists the XML-RPC is very slow in python.
	# Because we like speed we use direct http access to NZBGet to
	# obtain the result in JSON-format and then we parse it using low level
	# string functions. We could use python's json-module, which is
	# much faster than xmlrpc-module but it's still too slow.

	# Building http-URL to call method "listfiles" passing three parameters: (0, 0, nzb_id)
	httpUrl = 'http://%s:%s/jsonrpc/listfiles?1=0&2=0&3=%i' % (host, port, nzb_id);
	request = urllib2.Request(httpUrl)

	base64string = base64.encodestring('%s:%s' % (username, password)).replace('\n', '')

	request.add_header("Authorization", "Basic %s" % base64string)   

	# Load data from NZBGet
	response = urllib2.urlopen(request)
	data = response.read()
	# The "data" is a raw json-string. We could use json.loads(data) to
	# parse it but json-module is still slow. We parse it on our own.

	# Iterate through the list of files to find the last rar-file.
	# The last is the one with the highest XX in ".partXX.rar".
	regex = re.compile('.*\.part(\d+)\.rar', re.IGNORECASE)
	last_rar_file = None
	file_num = None
	file_id = None
	file_name = None
	
	for line in data.splitlines():
		if line.startswith('"ID" : '):
			cur_id = int(line[7:len(line)-1])
		if line.startswith('"Filename" : "'):
			cur_name = line[14:len(line)-2]
			match = regex.match(cur_name)
			if (match):
				cur_num = int(match.group(1))
				if not file_num or cur_num > file_num:
					file_num = cur_num
					file_id = cur_id
					file_name = cur_name

	# Move the last rar-file to the top of file list
	if (file_id):
		print('[INFO] Moving last rar-file to the top: %s' % file_name)
		# Using RPC-method "editqueue" of XML-RPC-object "nzbget".
		# we could use direct http access here too but the speed isn't
		# an issue here and XML-RPC is easier to use.
		nzbget.editqueue('FileMoveTop', 0, '', [file_id])
	else:
		print('[INFO] Skipping sorting since could not find any rar-files')
			
def main():
	# Globally define directory for storing list of tested files
	global tmp_file_name
	
	# Depending on the mode in which the script was called (queue-script
	# or post-processing-script) a different set of parameters (env. vars)
	# is passed. They also have different prefixes:
	#   - NZBNA_ in queue-script mode;
	#   - NZBPP_ in pp-script mode.
	Prefix = 'NZBNA_' if 'NZBNA_EVENT' in os.environ else 'NZBPP_'
	
	# Directory for storing list of tested files
	tmp_file_name = os.environ.get('NZBOP_TEMPDIR') + '/PasswordDetector/' + os.environ.get(Prefix + 'NZBID')
	
	# Do start up check
	start_check()

	# Read context (what nzb is currently being processed)
	Category = os.environ[Prefix + 'CATEGORY']
	Directory = os.environ[Prefix + 'DIRECTORY']
	NzbName = os.environ[Prefix + 'NZBNAME']
	NzbID = os.environ.get(Prefix + 'NZBID')	
	
	# Does nzb has a category from the list of categories to check?
	if not check_category(Category):
		print('[DETAIL] Skipping password detection for %s (not matching category)' % NzbName)
		sys.exit(POSTPROCESS_NONE)
	
	print('[DETAIL] Detecting password for %s' % NzbName)
	sys.stdout.flush()
	if os.environ.get('NZBNA_EVENT') == 'FILE_DOWNLOADED':
		if contains_password(Directory) is True:
			print("[WARNING] Password found in %s" % NzbName)
			print('[NZB] NZBPR_PASSWORDDETECTOR_HASPASSWORD=yes')
			if os.environ['NZBPO_PASSACTION'] == "Pause":
				pause_nzb(NzbID)
				print("[DETAIL] Paused %s" % NzbName)
			if os.environ['NZBPO_PASSACTION'] == "Mark Bad":
				print('[NZB] MARK=BAD')
				print("[DETAIL] Marked bad %s" % NzbName)
			
	# Remove temp file in PP
	if Prefix == 'NZBPP_':
		clean_up()
		
	print('[DETAIL] Detecting completed for %s' % NzbName)
	sys.stdout.flush()

main()
		
sys.exit(POSTPROCESS_SUCCESS)