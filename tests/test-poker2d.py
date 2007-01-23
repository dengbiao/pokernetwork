#!/usr/bin/python2.5
# -*- mode: python -*-
#
# Copyright (C) 2006 Mekensleep
#
# Mekensleep
# 24 rue vieille du temple
# 75004 Paris
#       licensing@mekensleep.com
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
# Foundation, Inc., 51 Franklin St, Fifth Floor, Boston, MA  02110-1301, USA.
#
# Authors:
#  Johan Euphrosine <johan@mekensleep.com>
#

import unittest
import sys
sys.path.insert(0, "..")
from pokerclient2d import poker2d
import platform

class MainMockup:    
    def __init__(self, config, settings):
        MainMockup.instance = self
        self.settings = settings
    def configOk(self):
        pass

class Poker2DTestCase(unittest.TestCase):
    def testConfigFileOnWindows(self):
        poker2d.Main = MainMockup
        system = platform.system
        platform.system = lambda : "Windows"
        os.environ["APPDATA"] = "conf"
        poker2d.run("", None, None)
        self.assertEqual(MainMockup.instance.settings, "conf/poker2d/poker2d.xml")
        platform.system = system

if __name__ == '__main__':
    unittest.main()

# Interpreted by emacs
# Local Variables:
# compile-command: "( cd .. ; ./config.status tests/test-poker2d.py ) ; ( cd ../tests ; make TESTS='test-poker2d.py' check )"
# End:
