# Copyright 2010 Google Inc. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import cgi
import json
import logging
import models
import os

from google.appengine.api import memcache
from google.appengine.api import users
from google.appengine.ext import webapp
from google.appengine.ext.webapp import template
from google.appengine.ext.webapp.util import run_wsgi_app
from google.appengine.ext import db

def ApplyStatisticsData(request, obj):
    """Applies statistics uploaded via the request to the object."""
    obj.using_spdy = bool(request.get('using_spdy')=="CHECKED")
    obj.start_load_time = int(request.get('start_load_time'))
    obj.commit_load_time = int(request.get('commit_load_time'))
    obj.doc_load_time = int(request.get('doc_load_time'))
    obj.paint_time = int(request.get('paint_time'))
    obj.total_time = int(request.get('total_time'))
    obj.last_load_time = int(request.get('last_load_time'))
    obj.num_requests = int(request.get('num_requests'))
    obj.num_connects = int(request.get('num_connects'))
    obj.num_sessions = int(request.get('num_sessions'))
    obj.read_bytes_kb = int(float(request.get('read_bytes_kb')))
    obj.write_bytes_kb = int(float(request.get('write_bytes_kb')))

def BandwidthPrettyString(bandwidth_kbps):
    if bandwidth_kbps > 1000:
        bandwidth_mbps = bandwidth_kbps / 1000.0
        return str(bandwidth_mbps) + "Mbps"
    return str(bandwidth_kbps) + "Kbps"

def NetworkPrettyString(item):
    network = ""
    network += BandwidthPrettyString(item.download_bandwidth_kbps)
    network += "/"
    network += BandwidthPrettyString(item.upload_bandwidth_kbps)
    network += "/"
    network += str(item.round_trip_time_ms)
    network += "ms/"
    network += str(item.packet_loss_rate)
    network += "%"
    if item.using_spdy:
        network += "/spdy"
    return network

class BaseRequestHandler(webapp.RequestHandler):
    def send_error(self, format, *args):
        """Send a fatal request error to the error log and response output."""
        logging.error(format, *args)
        self.response.out.write(format % args)

    def send_json_error(self, format, *args):
        """Send a fatal request error to the error log and json output."""
        logging.error(format, *args)
        json_error = {}
        json_error['error'] = format % args
        self.response.out.write(json.encode(json_error))


class JSONDataPage(BaseRequestHandler):
    """Do a search for TestSets."""
    def do_set_search(self):
        memcache_key = "set_search." + self.request.query
        cached_response = memcache.get(memcache_key)
        if cached_response is not None:
            self.response.out.write(cached_response)
            return

        query = models.TestSet.all()
        query.order("-date")

        # Apply filters.
        if self.request.get("networks_filter"):
            query.filter("network_type IN ", self.request.get("networks_filter").split(","))
        if self.request.get("version_filter"):
            query.filter("version IN ", self.request.get("version_filter").split(","))
        if self.request.get("set_id"):
            test_set = models.TestSet.get(db.Key(self.request.get("set_id")))
            results = test_set.summaries

        results = query.fetch(500)
        response = json.encode(results)
        memcache.add(memcache_key, response, 30)   # Cache for 30secs
        self.response.out.write(response)
    
    def do_set(self):
        """Lookup a specific TestSet."""
        set_id = self.request.get("id")
        if not set_id:
            self.send_json_error("Bad request, no id param")
            return
        test_set = models.TestSet.get(db.Key(set_id))
        if not test_set:
            self.send_json_error("Could not find id: ", id)
            return

        # We do manual coalescing of multiple data structures
        # into a single json blob.
        json_output = {}
        json_output['obj'] = test_set
        json_output['summaries'] = [s for s in test_set.summaries]
        self.response.out.write(json.encode(json_output))

    def do_summary(self):
        """ Lookup a specific TestSummary"""
        set_id = self.request.get("id")
        if not set_id:
            self.send_json_error("Bad request, no id param")
            return

        memcache_key = "summary." + set_id
        cached_response = memcache.get(memcache_key)
        if cached_response is not None:
            self.response.out.write(cached_response)
            return

        test_summary = models.TestSummary.get(db.Key(set_id))
        if not test_summary:
            self.send_json_error("Could not find id: ", id)
            return

        json_output = {}
        json_output['obj'] = test_summary
        test_set = models.TestSet.get(test_summary.set.key())
        test_results = test_set.results
        test_results.filter("url =", test_summary.url)
        json_output['results'] = [r for r in test_results]

        response = json.encode(json_output)
        memcache.add(memcache_key, response, 60)   # Cache for 1min
        self.response.out.write(response)

    def do_filters(self):
        """Lookup the distinct values in the TestSet data, for use in filtering.
        """
        cached_response = memcache.get("filters")
        if cached_response is not None:
            self.response.out.write(cached_response)
            return

        versions = set()
        networks = set()

        query = models.TestSet.all()
        for item in query:
            versions.add(item.version)
            networks.add(item.network_type)

        filters = {}
        filters["versions"] = sorted(versions)
        filters["networks"] = sorted(networks)
        response = json.encode(filters)
        memcache.add("filters", response, 60 * 30)  # Cache for 30 mins
        self.response.out.write(response)

    def do_latestresults(self):
        """Get the last 25 results posted to the server."""
        query = models.TestResult.all()
        query.order("-date")
        results = query.fetch(25)
        self.response.out.write(json.encode(results))

    def get(self):
        # TODO(mbelshe): the dev server doesn't properly handle logins?
        #if not user:
        #    self.redirect(users.create_login_url(self.request.uri))
        #    return

        resource_type = self.request.get("type")
        if not resource_type:
            self.send_json_error("Could not find type: ", type)
            return

        # Do a query for the appropriate resource type.
        if resource_type == "summary":
            self.do_summary()
            return
        elif resource_type == "result":
            # TODO(mbelshe): implement me!
            return
        elif resource_type == "set":
            self.do_set()
            return
        elif resource_type == "set_search":
            self.do_set_search()
            return
        elif resource_type == "filters":
            self.do_filters()
            return
        elif resource_type == "latestresults":
            self.do_latestresults()
            return

        self.response.out.write(json.encode({}))


class UploadTestSet(BaseRequestHandler):
    """Create an entry in the store for a new test."""
    def post(self):
        user = users.get_current_user()
        # TODO(mbelshe): the dev server doesn't properly handle logins?
        #if not user:
        #    self.redirect(users.create_login_url(self.request.uri))
        #    return

        cmd = self.request.get("cmd")
        if not cmd:
            self.send_error("Bad request, no cmd param")
            return
   
        if cmd == "create":
            test_set = models.TestSet(user=user)
            test_set.notes = self.request.get('notes')
            test_set.cmdline  = self.request.get('cmdline')
            test_set.version  = self.request.get('version')
            test_set.platform  = self.request.get('platform')
            test_set.client_hostname  = self.request.get('client_hostname')
            test_set.download_bandwidth_kbps = int(self.request.get('download_bandwidth_kbps'))
            test_set.upload_bandwidth_kbps = int(self.request.get('upload_bandwidth_kbps'))
            test_set.round_trip_time_ms = int(self.request.get('round_trip_time_ms'))
            test_set.packet_loss_rate  = float(self.request.get('packet_loss_rate'))
            test_set.packet_loss_rate  = float(self.request.get('packet_loss_rate'))
            test_set.using_spdy = bool(self.request.get('using_spdy')=="CHECKED")
            test_set.network_type = NetworkPrettyString(test_set)
            key = test_set.put()
            self.response.out.write(key)

        elif cmd == "update":
            set_id = self.request.get("set_id")
            if not set_id:
                self.send_error("Bad request, no set_id param")
                return
            test_set = models.TestSet.get(db.Key(set_id))
            if not test_set:
                self.send_error("Could not find set_id: ", set_id)
                return
            ApplyStatisticsData(self.request, test_set)
            test_set.iterations = int(self.request.get('iterations'))
            test_set.url_count = int(self.request.get('url_count'))
            key = test_set.put()
            self.response.out.write(key)
        else:
            self.send_error("Bad request, unknown cmd: %s", cmd)

class UploadTestResult(BaseRequestHandler):
    """Create an entry in the store for a new test run."""
    def post(self):
        user = users.get_current_user()
        # TODO(mbelshe): the dev server doesn't properly handle logins?
        #if not user:
        #    self.redirect(users.create_login_url(self.request.uri))
        #    return

        set_id = self.request.get('set_id')
        if not set_id:
            self.send_error("Bad request, no set_id param")
            return
        test_set = models.TestSet.get(db.Key(set_id))
        if not test_set:
            self.send_error("Could not find set_id: ", set_id)
            return
        my_url = self.request.get('url')

        test_result = models.TestResult(set=test_set, url=my_url)
        ApplyStatisticsData(self.request, test_result)
        key = test_result.put()
        self.response.out.write(key)

class UploadTestSummary(BaseRequestHandler):
    def post(self):
        user = users.get_current_user()
        # TODO(mbelshe): the dev server doesn't properly handle logins?
        #if not user:
        #    self.redirect(users.create_login_url(self.request.uri))
        #    return

        set_id = self.request.get('set_id')
        if not set_id:
            self.send_error("Bad request, no set_id param")
            return
        test_set = models.TestSet.get(db.Key(set_id))
        if not test_set:
            self.send_error("Could not find set_id: ", set_id)
            return
        my_url = self.request.get('url')

        test_summary = models.TestSummary(set=test_set, url=my_url)
        ApplyStatisticsData(self.request, test_summary)
        test_summary.iterations = int(self.request.get('iterations'))
        test_summary.total_time_stddev = float(self.request.get('total_time_stddev'))
        key = test_summary.put()
        self.response.out.write(key)

application = webapp.WSGIApplication(
                                     [
                                      ('/set', UploadTestSet),
                                      ('/result', UploadTestResult),
                                      ('/summary', UploadTestSummary),
                                      ('/json', JSONDataPage),
                                     ],
                                     debug=True)

def main():
    run_wsgi_app(application)

if __name__ == "__main__":
    main()
