#!/usr/bin/env python
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

"""Replays web pages under simulated network conditions.

Must be run as administrator (sudo).

To record web pages:
  1. Start the program in record mode.
     $ sudo ./replay.py --record archive.wpr
  2. Load the web pages you want to record in a web browser. It is important to
     clear browser caches before this so that all subresources are requested
     from the network.
  3. Kill the process to stop recording.

To replay web pages:
  1. Start the program in replay mode with a previously recorded archive.
     $ sudo ./replay.py archive.wpr
  2. Load recorded pages in a web browser. A 404 will be served for any pages or
     resources not in the recorded archive.

Network simulation examples:
  # 128KByte/s uplink bandwidth, 4Mbps/s downlink bandwidth with 100ms RTT time
  $ sudo ./replay.py --up 128KByte/s --down 4Mbit/s --delay_ms=100 archive.wpr

  # 1% packet loss rate
  $ sudo ./replay.py --packet_loss_rate=0.01 archive.wpr
"""

import dnsproxy
import httpproxy
import logging
import optparse
import platformsettings
import socket
import sys
import threading
import time
import traceback
import trafficshaper


if sys.version < '2.6':
  print 'Need Python 2.6 or greater.'
  sys.exit(1)


def get_server_address(options):
  if options.server_mode:
    return socket.gethostbyname(socket.gethostname())
  return '127.0.0.1'


def point_dns(server):
  platform_settings = platformsettings.get_platform_settings()
  try:
    platform_settings.set_primary_dns(options.server)
    while (True):
      time.sleep(1)
  except KeyboardInterrupt:
    logging.info('Shutting down.')
  finally:
    platform_settings.restore_primary_dns()


def main(options, args):
  if options.server:
    point_dns(options.server)
    return

  if options.record:
    replay_server_class = httpproxy.RecordHttpProxyServer
  elif options.spdy:
    # TODO(lzheng): move this import to the front of the file once
    # nbhttp moves its logging config in server.py into main.
    import replayspdyserver
    replay_server_class = replayspdyserver.ReplaySpdyServer
  else:
    replay_server_class = httpproxy.ReplayHttpProxyServer

  try:
    replay_file = args[0]
    host = get_server_address(options)

    with dnsproxy.DnsProxyServer(
        options.dns_forwarding,
        options.dns_private_passthrough,
        host=host) as dns_server:
      with replay_server_class(
          replay_file,
          options.deterministic_script,
          dns_server.real_dns_lookup,
          host=host,
          port=options.port,
          use_ssl=options.spdy != "no-ssl",
          certfile=options.certfile,
          keyfile=options.keyfile):
        with trafficshaper.TrafficShaper(
            host,
            options.port,
            options.up,
            options.down,
            options.delay_ms,
            options.packet_loss_rate,
            options.init_cwnd):
          while (True):
            time.sleep(1)
  except KeyboardInterrupt:
    logging.info('Shutting down.')
  except dnsproxy.DnsProxyException, e:
    logging.critical(e)
  except trafficshaper.TrafficShaperException, e:
    logging.critical(e)
  except:
    print traceback.format_exc()


if __name__ == '__main__':
  class PlainHelpFormatter(optparse.IndentedHelpFormatter):
    def format_description(self, description):
      if description:
        return description + '\n'
      else:
        return ''

  option_parser = optparse.OptionParser(
      usage='%prog [options] replay_file',
      formatter=PlainHelpFormatter(),
      description=__doc__,
      epilog='http://code.google.com/p/web-page-replay/')

  option_parser.add_option('-s', '--spdy', default=False,
      action='store',
      type='string',
      help='Use spdy to replay relay_file.  --spdy="no-ssl" uses SPDY without SSL.')
  option_parser.add_option('-r', '--record', default=False,
      action='store_true',
      help='Download real responses and record them to replay_file')
  option_parser.add_option('-l', '--log_level', default='debug',
      action='store',
      type='choice',
      choices=('debug', 'info', 'warning', 'error', 'critical'),
      help='Minimum verbosity level to log')
  option_parser.add_option('-f', '--log_file', default=None,
      action='store',
      type='string',
      help='Log file to use in addition to writting logs to stderr.')

  network_group = optparse.OptionGroup(option_parser,
      'Network Simulation Options',
      'These options configure the network simulation in replay mode')
  network_group.add_option('-u', '--up', default='0',
      action='store',
      type='string',
      help='Upload Bandwidth in [K|M]{bit/s|Byte/s}. Zero means unlimited.')
  network_group.add_option('-d', '--down', default='0',
      action='store',
      type='string',
      help='Download Bandwidth in [K|M]{bit/s|Byte/s}. Zero means unlimited.')
  network_group.add_option('-m', '--delay_ms', default='0',
      action='store',
      type='string',
      help='Propagation delay (latency) in milliseconds. Zero means no delay.')
  network_group.add_option('-p', '--packet_loss_rate', default='0',
      action='store',
      type='string',
      help='Packet loss rate in range [0..1]. Zero means no loss.')
  network_group.add_option('-w', '--init_cwnd', default='0',
      action='store',
      type='string',
      help='Set initial cwnd (linux only, requires kernel patch)')
  option_parser.add_option_group(network_group)

  harness_group = optparse.OptionGroup(option_parser,
      'Replay Harness Options',
      'These advanced options configure various aspects of the replay harness')
  harness_group.add_option('-S', '--server', default=None,
      action='store',
      type='string',
      help='Don\'t run replay and traffic shaping. Instead connect to another'
           ' instance running --server_mode on port 80 of the given IP. NOTE:'
           ' The same may be accomplished by updating DNS to point to server')
  harness_group.add_option('-M', '--server_mode', default=False,
      action='store_true',
      help='Don\'t forward local traffic to the replay server. Instead, only'
           ' serve the replay and traffic shaping functionality on --port.'
           ' Other instances may connect to this using --server or by pointing'
           ' their DNS to this server.')
  harness_group.add_option('-n', '--no-deterministic_script', default=True,
      action='store_false',
      dest='deterministic_script',
      help='Don\'t inject JavaScript which makes sources of entropy such as '
           'Date() and Math.random() deterministic. CAUTION: With this option '
           'many web pages will not replay properly.')
  harness_group.add_option('-P', '--no-dns_private_passthrough', default=True,
      action='store_false',
      dest='dns_private_passthrough',
      help='Don\'t forward DNS requests that resolve to private network '
           'addresses. CAUTION: With this option important services like '
           'Kerberos will resolve to the HTTP proxy address.')
  harness_group.add_option('-x', '--no-dns_forwarding', default=True,
      action='store_false',
      dest='dns_forwarding',
      help='Don\'t forward DNS requests to the local replay server.'
           'CAUTION: With this option an external mechanism must be used to '
           'forward traffic to the replay server.')
  harness_group.add_option('-o', '--port', default=80,
      action='store',
      type='int',
      help='Port number to listen on. CAUTION: Normal replay functionality '
           'relies on using port 80.')
  harness_group.add_option('-c', '--certfile', default='',
      action='store',
      dest='certfile',
      type='string',
      help='Certificate file for use with SSL')
  harness_group.add_option('-k', '--keyfile', default='',
      action='store',
      dest='keyfile',
      type='string',
      help='Key file for use with SSL')
  option_parser.add_option_group(harness_group)

  options, args = option_parser.parse_args()

  log_level = logging.__dict__[options.log_level.upper()]
  logging.basicConfig(level=log_level,
                      format='%(asctime)s %(levelname)s %(message)s')

  if options.log_file:
    fh = logging.FileHandler(options.log_file)
    fh.setLevel(log_level)
    logging.getLogger('').addHandler(fh)

  if not options.server and len(args) != 1:
    option_parser.error('Must specify a replay_file')

  if options.record:
    if options.up != '0':
      option_parser.error('Option --up cannot be used with --record.')
    if options.down != '0':
      option_parser.error('Option --down cannot be used with --record.')
    if options.delay_ms != '0':
      option_parser.error('Option --delay_ms cannot be used with --record.')
    if options.packet_loss_rate != '0':
      option_parser.error(
          'Option --packet_loss_rate cannot be used with --record.')
    if options.spdy:
      option_parser.error('Option --spdy cannot be used with --record.')

  if options.server and options.server_mode:
    option_parser.error('Cannot run with both --server and --server_mode')

  if options.spdy and options.deterministic_script:
    logging.warning(
        'Option --deterministic-_script is ignored with --spdy.'
        'See http://code.google.com/p/web-page-replay/issues/detail?id=10')

  sys.exit(main(options, args))
