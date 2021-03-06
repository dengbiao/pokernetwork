#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright (C) 2006, 2007, 2008       Loic Dachary <loic@dachary.org>
# Copyright (C)             2008, 2009 Bradley M. Kuhn <bkuhn@ebb.org>
# Copyright (C)                   2009 Johan Euphrosine <proppy@aminche.com>
#
# This software's license gives you freedom; you can copy, convey,
# propagate, redistribute and/or modify this program under the terms of
# the GNU Affero General Public License (AGPL) as published by the Free
# Software Foundation (FSF), either version 3 of the License, or (at your
# option) any later version of the AGPL published by the FSF.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU Affero
# General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program in a file in the toplevel directory called
# "AGPLv3".  If not, see <http://www.gnu.org/licenses/>.
#
import sys
import re
from os import path

TESTS_PATH = path.dirname(path.realpath(__file__))
sys.path.insert(0, path.join(TESTS_PATH, ".."))

from config import config
from log_history import log_history

import libxml2
import time

from tests import testclock
from twisted.trial import unittest, runner, reporter
import twisted.internet.base
from twisted.internet import reactor, defer

try:
    from collections import OrderedDict
except ImportError:
    from pokernetwork.util.ordereddict import OrderedDict

from random import seed, randint
import copy

twisted.internet.base.DelayedCall.debug = False

from pokernetwork.pokertable import PokerPredefinedDecks
import reflogging
log = reflogging.root_logger.get_child('test-pokertable')

from pokerengine import pokertournament
from pokernetwork import pokertable, pokernetworkconfig
from pokerpackets.packets import *
from pokerpackets.networkpackets import *
from pokernetwork.pokeravatar import DEFAULT_PLAYER_USER_DATA, PokerAvatar
from pokerengine.pokercards import PokerCards

try:
    from nose.plugins.attrib import attr
except ImportError:
    attr = lambda *args, **kw: lambda fn: fn

global table1ID
global table2ID
global table3ID
global table9ID
table1ID = 100
table2ID = 200
table3ID = 300
table9ID = 900
settings_xml = """<?xml version="1.0" encoding="UTF-8"?>
<server verbose="4" autodeal="yes" max_missed_round="5">
  <delays autodeal_tournament_min="2" autodeal="2" autodeal_max="2" autodeal_check="0" round="0" position="0" showdown="0" finish="0" />

  <path>%(engine_path)s/conf %(tests_path)s/../conf</path>
  <users temporary="BOT.*"/>
</server>
""" % {
    'tests_path': TESTS_PATH,
    'engine_path': config.test.engine_path
}
settings_stripped_deck_no_autodeal_xml = """<?xml version="1.0" encoding="UTF-8"?>
<server verbose="4" autodeal="no" >
  <delays autodeal_tournament_min="2" autodeal="2" autodeal_max="2" autodeal_check="0" round="0" position="0" showdown="0" finish="0" />

  <decks>
    <deck>9c 9d 9h Ts Tc Td Th Ts Jc Jd Jh Js Qc Qd Qh Qs Kc Kd Kh Ks Ac Ad Ah As</deck>
  </decks>

  <path>%(engine_path)s/conf %(tests_path)s/../conf</path>
  <users temporary="BOT.*"/>
</server>
""" % {
    'tests_path': TESTS_PATH,
    'engine_path': config.test.engine_path
}

board = PokerCards() 
hand1 = PokerCards(['Qd', 'Ts'])
hand2 = PokerCards(['Kh', 'Kc'])
flop  = PokerCards(['Jd', 'Js', "Jc"])
turn  = PokerCards(['Tc', 'Js', 'Jc', 'Tc'])
river  = PokerCards(['Tc', 'Js', 'Jc', 'Tc', 'Ad'])

exampleHand =  [ \
        ('wait_for', 1, 'first_round'), \
        ('player_list', [1, 2]), \
        ('round', 'round1', board, { 1 : hand1, 2 : hand2}), \
        ('round', 'round2', flop, { 1 : hand1, 2 : hand2}), \
        ('round', 'round3', turn, { 1 : hand1, 2 : hand2}), \
        ('round', 'round4', river, { 1 : hand1, 2 : hand2}), \
# Round 5 doesn't changed the board to test certain code in
# compressedHistory: it expects that if you haven't changed the board in a
# new round, the history gives you "None"
        ('round', 'round5', river, { 1 : hand1, 2 : hand2}), \
        ('showdown', river, {1 : hand1, 2 : hand2}), \
        ('position', 1, 13), \
        ('blind_request', 1, 222, 735, 'big_and_dead'), \
        ('wait_blind', 1), \
        ('blind', 1, 222, 0), \
        ('ante_request', 1, 111), \
        ('ante', 1, 111), \
        ('ante', 5, 555), \
        ('all-in', 1), \
        ('call', 1, 411), \
        ('call', 6, 626), \
        ('check', 1), \
        ('fold', 1), \
        ('raise', 1, 888), \
# Note: 3 appears first here to test something appearing first as a raise.
        ('raise', 10, 976), \
        ('canceled', 4, 10), \
        ('rake', 7, { 1 : 7}), \
        ('end', [8, 1], [{ 'serial2share': { 8: 888, 1: 233 }, 'serial2money' : {8: 8888, 1:33} }]), \
        ('sitOut', 1), \
        ('leave', [(1, 2), (2, 7)]), \
        ('finish', 1), \
        ('muck', (1,2)), \
        ('rebuy', 1, 9999), \
        ('unknown',) ]
def convertHandHistoryToDict (hist):
    dict = {}
    for entry in hist:
        key = entry[0]
        dict[key] = entry[1:-1]
    return dict

class MockChatFilter:
    def sub(self,repl,message):
        return message

class MockService:
            
    def __init__(self, settings):
        self.settings = settings
        self.dirs = settings.headerGet("/server/path").split()
        self.simultaneous = 1
        self.shutting_down = False
        self.hand_serial = 0
        self.hands = {}
        self.chat = False
        self.players = {}
        self.table1 = None
        self.table2 = None
        self.testObject = None
        self.joined_count = 0
        self.joined_max = 1000
        self.chat_messages = []
        self.chat_filter = MockChatFilter()
        self.temporary_serial_min = 0
        self.temporary_serial_max = 0
        self.temporary_users_pattern = '^BOT.*$'

    def getMissedRoundMax(self):
        return 5  # if you change this, change it in settings_xml above

    # Just copied these joinedCount functions from the real service.
    def joinedCountReachedMax(self):
        """Returns True iff. the number of joins to tables has exceeded
        the maximum allowed by the server configuration"""
        return self.joined_count >= self.joined_max

    def joinedCountIncrease(self, num = 1):
        """Increases the number of currently joins to tables by num, which
        defaults to 1."""
        self.joined_count += num
        return self.joined_count

    def joinedCountDecrease(self, num = 1):
        """Decreases the number of currently joins to tables by num, which
        defaults to 1."""
        self.joined_count -= num
        return self.joined_count

    def getTable(self, gameId):
        if gameId == self.table1.game.id:
            return self.table1
        elif gameId == self.table2.game.id:
            return self.table2
        else:
            self.error("Unknown game requested: " + gameId)
            return None

    def message(self, message):
        print "MockService " + message
        
    def getName(self, serial):
        return "MockServicePlayerName%d" % serial

    def getPlayerInfo(self, serial):
        class Dummy:
            def __init__(self):
                self.name = "MockServicePlayerInfo"
                self.url = "MockServicePlayerInfo.url"
                self.outfit = "MockServicePlayerInfo.outfit"
        return Dummy()

    def error(self, message):
        self.message("error " + message)

    def movePlayer(self, serial, fromGameId, toGameId):
        return 0

    def seatPlayer(self, serial, table_id, amount, minimum_amount = None):
        if serial in self.players:
            self.error("Player is already seated at table, ." % table_id)
            return False
        else:
            self.players[serial] = { 'table_id' : table_id, 'amount' : amount }
            return True

    def buyInPlayer(self, serial, game_id, currency_serial, amount):
        if serial == 9 and amount != 20 and amount != 200:
            return 0
        else:
            return amount

    def createHand(self, game_id, tourney_serial=None):
        self.hand_serial += 1
        return self.hand_serial

    def leavePlayer(self, serial, table_id, currency_serial):
        if serial in self.players:
            del self.players[serial]
            return True
        else:
            self.error("Player is already seated at table, %d." % table_id)
            return False
        
    def despawnTable(self, x):
        pass
    
    def deleteTable(self, x):
        pass

    def destroyTable(self, x):
        pass

    def eventTable(self, table):
        pass

    def updateTableStats(self, game, observers, waiting):
        pass
    
    def loadHand(self, handId, removeList = []):
        # Only ever return the one hand; the only one this mock game ever had...
        #  ... but only if they give a positive integer as a handId.
        if handId <= 0:
            return None
        else:
            l = copy.deepcopy(exampleHand)
            # Remove anything that this specific test wants to get rid of,
            # perhaps because it messes with their results.
            for xx in removeList:
                l.remove(xx)
            l.insert(0, ('game', 1, handId, 3, time.time(), 'variant','betting_structure', [1, 2], 7, { 1 : 7890, 2 : 1234, 'values' : ''}))
            self.hands[handId] = convertHandHistoryToDict(l)
            return l

    def saveHand(self, history, serial):
        if self.testObject:
            historyDict = convertHandHistoryToDict(history)
            handId = historyDict['game'][1]
            origDict = self.hands[handId]
            for (action, fields) in historyDict.iteritems():
                if action == "showdown":
                    self.testObject.failUnless(fields == (None,) or fields == (PokerCards([34, 48, 35, 34, 25, 20]),))
                elif action == "round" and fields[0] == "round5":
                    self.testObject.assertEqual(fields, ('round5', None,))
                else:
                    self.testObject.assertEqual(origDict[action], fields)

    def updatePlayerMoney(self, serial, gameId, amount):
        # Most of this function matches up with the false hand history above
        #  Compare it to that when figuring out where these numbers come from,
        #  except for serial 3, which is based on the ante he makes in test21
        if self.testObject:
            self.testObject.assertEqual(gameId,  self.testObject.table1_value)
            if serial == 1:
                self.testObject.assertEqual(amount, -1399)
            elif serial == 3:
                self.testObject.assertEqual(amount, -1)
            elif serial == 10:
                self.testObject.assertEqual(amount, -976)
            elif serial == 4:
                self.testObject.assertEqual(amount, 10)
            elif serial == 5:
                self.testObject.assertEqual(amount, -555)
            elif serial == 6:
                self.testObject.assertEqual(amount, -626)
            elif serial == 8:
                self.testObject.assertEqual(amount, 888)
            else:
                self.testObject.fail("Unkown serial in hand history: %d" % serial)
                
    def updatePlayerRake(self, currencySerial, serial, rakeAmount):
        if self.testObject:
            self.testObject.assertEqual(rakeAmount, 7)
            self.testObject.assertEqual(serial, 1)

    def tourneyEndTurn(self, tourney, game_id):
        if self.testObject:
            self.testObject.assertEqual(game_id, self.testObject.table1_value)
            self.testObject.assertEqual(tourney.name, 'My Old Sit and Go')
    
    def tourneyUpdateStats(self,tourney, game_id):
        if self.testObject:
            self.testObject.assertEqual(game_id, self.testObject.table1_value)
            self.testObject.assertEqual(tourney.name, 'My Old Sit and Go')

    def databaseEvent(self, event, **kwargs):
        if self.testObject:
            self.testObject.assertEqual(PacketPokerMonitorEvent.HAND, event)

    def chatMessageArchive(self, player_serial, game_id, message):
        self.chat_messages.append((player_serial, game_id, message))
    
    def isTemporaryUser(self,serial):
        return bool(
            self.temporary_serial_min <= serial <= self.temporary_serial_max or 
            re.match(self.temporary_users_pattern,self.getName(serial))
        )
    def tourneySerialsRebuying(self, tournament, game_id):
        return set()

    def tourneyRebuyAllPlayers(self, tournament, game_id):
        pass
            
class MockClient:
    log = log.get_child('MockClient')
    
    class User:
        def isLogged(self):
            return True
        
    def __init__(self, serial, testObject, expectedReason = ""):
        self.log = MockClient.log.get_instance(self, refs=[
            ('User', self, lambda avatar: avatar.serial)
        ])
        
        self.serial = serial
        self.deferred = None
        self.raise_if_packet = None
        self.type = None
        self.tables = {}
        self.packets = []
        self.user = MockClient.User()
        self.testObject = testObject
        self.reasonExpected = expectedReason
        self.bugous_processing_hand = False

    def __str__(self):
        return "MockClient of Player%d" % self.serial

    def waitFor(self, type):
        self.deferred = defer.Deferred()
        self.type = type
        return self.deferred
    
    def raiseIfPacket(self, type):
        self.raise_if_packet = type

    def lookForPacket(self, type):
        for packet in self.packets:
            if packet.type == type:
                return packet
        return False

    def message(self, message):
        print "MockClient " + message
        
    def error(self, message):
        self.message("error " + message)

    def join(self, table, reason = ""):
        self.testObject.assertEquals(reason, self.reasonExpected)

    # Loic indicates that it's the job of the Client to pass along a few
    # things, including "player removes", and various clients settings, to
    # the game class.  These next few functions do that to be consistent
    # with what the pokertable API expects.

    def removePlayer(self, table, serial):
        if table.game.id not in self.tables:
            self.log.warn("Table with game number %d does not occur exactly once for this player.", table.game.id)
        if serial == 9:
            table.game.removePlayer(serial)
            return False
        return table.game.removePlayer(serial)

    def autoBlindAnte(self, table, serial, auto):
        table.game.getPlayer(serial).auto_blind_ante = auto

    def addPlayer(self, table, seat):
        self.tables[table.game.id] = table
        if table.game.addPlayer(self.serial, seat):
            player = table.game.getPlayer(self.serial)
            player.setUserData(DEFAULT_PLAYER_USER_DATA.copy())
        return True

    def sendPacket(self, packet):
        self.log.debug("sendPacket: %s", packet)
        self.packets.append(packet)
        if self.deferred:
            if self.raise_if_packet and packet.type == self.raise_if_packet:
                d, self.deferred = self.deferred, None
                raise_if_packet, self.raise_if_packet = self.raise_if_packet, None
                reactor.callLater(0, d.errback, packet)
            elif self.type == packet.type:
                d, self.deferred = self.deferred, None
                packet_type, self.type = self.type, None
                reactor.callLater(0, d.callback, packet)

    def sendPacketVerbose(self, packet):
        self.sendPacket(packet)

    def getSerial(self):
        return self.serial

    def setMoney(self, table, amount):
        return table.game.payBuyIn(self.serial, amount)

    def getName(self):
        return "Player%d" % self.serial

    def getPlayerInfo(self):
        class MockPlayerInfo:
            def __init__(self, player):
                self.player = player
                self.name = self.player.getName()
                self.url = "http://fake"
                self.outfit = ""
        return MockPlayerInfo(self)
    
    def buyOutPlayer(self, table, serial):
        pass

# --------------------------------------------------------------------------------
class MockClientBot(MockClient):
    def getName(self):
        return "BOT%d" % self.serial
# --------------------------------------------------------------------------------
class MockClientWithTableDict(MockClient):
    def __init__(self, serial, testObject):
        self.tables = {}
        MockClient.__init__(self, serial, testObject)

    def addPlayer(self, table, seat):
        MockClient.addPlayer(self, table, seat)
        self.tables[table.game.id] = seat
# --------------------------------------------------------------------------------
class MockClientWithRemoveTable(MockClient):
    def removeTable(self, gameId):
        return True
# --------------------------------------------------------------------------------
class MockClientWithRealJoin(MockClient, PokerAvatar):
    def join(self, table, reason=""):
        PokerAvatar.join(self, table, reason)
# --------------------------------------------------------------------------------
class MockClientWithExplain(MockClientWithRealJoin):
    addPlayer = PokerAvatar.addPlayer
    
    def __init__(self,*args,**kw):
        self.explain = None
        MockClientWithRealJoin.__init__(self,*args,**kw)
        
    def setExplain(self,what):
        ret = PokerAvatar.setExplain(self, what)
        if ret:
            self.packets_explained = []
            self.explain._prefix = '[%d] ' % self.getSerial()
        return ret
        
    def sendPacket(self,packet):
        if self.explain:
            self.explain.explain(packet)
            packets = self.explain.forward_packets
        return MockClientWithRealJoin.sendPacket(self,packet)
    
    def setMoney(self, table, amount):
        return PokerAvatar.setMoney(self, table, amount)
    
# --------------------------------------------------------------------------------
class MockAvatar():
    def __init__(self, serial):
        self.serial = serial
    def getSerial(self):
        return self.serial


class PokerAvatarCollectionTestCase(unittest.TestCase):

    def test01(self):
        avatar_collection = pokertable.PokerAvatarCollection(prefix = '')
        serial1 = 200
        serial2 = 400

        self.assertEquals([], avatar_collection.get(serial1))
    
        avatar1a = MockAvatar(serial1)
        avatar1b = MockAvatar(serial1)
        avatar2 = MockAvatar(serial2)

        avatars = [ avatar1a, avatar1b ]
        for avatar in avatars:
            avatar_collection.add(avatar)

        self.assertEquals(avatars, avatar_collection.get(serial1))
        avatar_collection.remove(avatar1a)
        self.assertRaises(AssertionError, avatar_collection.remove, avatar1a)
        self.assertEquals([avatar1b], avatar_collection.get(serial1))
        avatar_collection.add(avatar1b) # add twice is noop
        self.assertEquals([[avatar1b]], avatar_collection.values())
        avatar_collection.add(avatar1a)

        self.assertEquals([avatar1b, avatar1a], avatar_collection.get(serial1))
        avatar_collection.add(avatar2)
        self.assertEquals([avatar2], avatar_collection.get(serial2))
        avatar_collection.remove(avatar2)
        self.assertEquals([], avatar_collection.get(serial2))
        
    def test02_isEmpty(self):
        avatar_collection = pokertable.PokerAvatarCollection(prefix = '')
        self.assertTrue(avatar_collection.isEmpty())
        avatar1 = MockAvatar(1)
        avatar_collection.add(avatar1)
        self.assertFalse(avatar_collection.isEmpty())

# --------------------------------------------------------------------------------
class PokerTableTestCaseBase(unittest.TestCase):
    # -------------------------------------------------------------------
    def setUp(self, settingsXmlStr=settings_xml, ServiceClass = MockService):
        testclock._seconds_reset()        
        global table1ID
        global table2ID
        global table3ID
        global table9ID
        table1ID = table1ID + 1
        table2ID += 1
        table3ID += 1
        table9ID += 1
        self.table1_value = table1ID
        self.table2_value = table2ID
        self.table3_value = table3ID
        self.table9_value = table9ID

        settings = pokernetworkconfig.Config([])
        settings.doc = libxml2.parseMemory(settingsXmlStr, len(settingsXmlStr))
        settings.header = settings.doc.xpathNewContext()
        self.service = ServiceClass(settings)
        self.table = pokertable.PokerTable(self.service, table1ID, {
            'name': "table1",
            'variant': "holdem",
            'betting_structure': "1-2_20-200_limit",
            'seats': 4,
            'player_timeout' : 6, 
            'muck_timeout' : 1,
            'currency_serial': 0,
            'max_missed_round' : 3
        })
        self.table2 = pokertable.PokerTable(self.service, table2ID, {
            'name': "table2",
            'variant': "holdem",
            'betting_structure': "1-2_20-200_limit",
            'seats': 4,
            'player_timeout' : 6, 
            'muck_timeout' : 1,
            'currency_serial': 0
        })
        self.table3 = pokertable.PokerTable(self.service, table3ID, {
            'name': "table3",
            'variant': "holdem",
            'betting_structure': "1-2_20-200_no-limit",
            'seats': 4,
            'player_timeout' : 6, 
            'muck_timeout' : 1,
            'currency_serial': 0
        })
        self.table9 = pokertable.PokerTable(self.service, table9ID, {
            'name': "table9",
            'variant': "holdem",
            'betting_structure': "1-2_20-200_no-limit",
            'seats': 9,
            'player_timeout' : 6, 
            'muck_timeout' : 1,
            'currency_serial': 0
        })
        self.service.table1 = self.table
        self.service.table2 = self.table2
        self.service.table3 = self.table3
        self.service.table9 = self.table9

        # Test to make sure that the max_missed_round count can be
        # overwritten by description in init settings.  note That table1
        # has setting of '1' for that value, but table2 accepts the
        # default above.
        self.assertEquals(self.table.max_missed_round, 3)
        self.assertEquals(self.table2.max_missed_round, 5)

        self.clients = {}
        
        # self.table's game has a special remove player function that always
        # fails for serial 9
        _removePlayer = self.table.game.removePlayer
        def fakeGameRemovePlayer(serial):
            ret = _removePlayer(serial)
            return False if serial == 9 else ret
        self.table.game.removePlayer = fakeGameRemovePlayer
        
    # -------------------------------------------------------------------
    def tearDown(self):
        self.table._lock_check.stop()
        self.table.cancelDealTimeout()
        self.table.cancelPlayerTimers()
        del self.table
        del self.service
    # -------------------------------------------------------------------
    def createPlayer(self, serial, getReadyToPlay=True, clientClass=MockClient, table=None):
        if table == None:
            table = self.table
        client = clientClass(serial, self)
        self.clients[serial] = client
        if getReadyToPlay:
            client.reasonExpected = "MockCreatePlayerJoin"
            self.assertEqual(True, table.joinPlayer(client, reason="MockCreatePlayerJoin"))
            client.reasonExpected = ""
            self.assertEqual(True, table.seatPlayer(client, -1))
            self.assertEqual(True, table.buyInPlayer(client, self.table.game.maxBuyIn()))
            self.table.sitPlayer(client)
        return client
    # -------------------------------------------------------------------
    def createBot(self, serial, getReadyToPlay=True, clientClass=MockClientBot, table=None):
        return self.createPlayer(serial, getReadyToPlay, clientClass, table)
# --------------------------------------------------------------------------------
class PokerTableTestCase(PokerTableTestCaseBase):
    # -------------------------------------------------------------------
    def test01_autodeal(self):
        self.createPlayer(1)
        self.createPlayer(2)
        # test is run also in subclass where self.table.autodeal is False, hence the check for self.table.autodeal
        self.assertEquals(self.table.autodeal, self.table.scheduleAutoDeal())
    # -------------------------------------------------------------------
    def test01_4_autodealTemporarySerials(self):
        """Test that autodeal won't happen if the only users connected have 
        serials in the range of being categorized temporary."""
        self.service.temporary_serial_min, self.service.temporary_serial_max = 100, 110
        self.createPlayer(100)
        self.createPlayer(110)
        self.assertEquals(False, self.table.scheduleAutoDeal())
        
    # -------------------------------------------------------------------
    def test01_5_autodealWithTemporaryUsers(self):
        """Test that autodeal won't happen when it's all bots sitting down."""
        self.service.temporary_users_pattern = '^MockServicePlayerName.*$'
        self.createPlayer(1)
        self.createPlayer(2)
        self.assertEquals(False, self.table.scheduleAutoDeal())
        
    # -------------------------------------------------------------------
    def test01_6_autodealWithBotsDealTemporary(self):
        """Test that autodeal will happen when it's all bots sitting down, 
        and autodeal_temporary is set on true"""
        self.service.temporary_users_pattern = '^MockServicePlayerName.*$'
        self.createPlayer(1)
        self.createPlayer(2)
        self.table.autodeal_temporary = True
        # test is run also in subclass where self.table.autodeal is False, hence the check for self.table.autodeal
        self.assertEquals(self.table.autodeal, self.table.scheduleAutoDeal())
        
    # -------------------------------------------------------------------
    def test01_7_autodealShutDown(self):
        self.createPlayer(1)
        self.createPlayer(2)
        self.service.shutting_down = True
        self.assertEquals(False, self.table.scheduleAutoDeal())

    # -------------------------------------------------------------------
    def test02_autodeal_check(self):
        self.createPlayer(1)
        self.table.processingHand(1)
        self.table.game_delay["delay"] = 2
        self.table.game_delay["start"] = testclock._seconds_value
        self.createPlayer(2)
        self.table.scheduleAutoDeal()
        return self.clients[2].waitFor(PACKET_POKER_MESSAGE)
    # -------------------------------------------------------------------
    def test_02_1_autodeal_destroy(self):
        self.createPlayer(1)
        self.createPlayer(2)
        self.table.processingHand(1)
        self.table.autoDealCheck(20, 10)
        dealTimeout = self.table.timer_info["dealTimeout"]
        self.table.destroy()
        self.assertEquals(1, dealTimeout.cancelled)
        self.assertEquals(False, "dealTimeout" in self.table.timer_info)
    # -------------------------------------------------------------------
    def test03_scheduleAutoDeal_should_not_call_beginTurn(self):
        gen = (e for e in (True, False))
        def shouldAutoDealNew():
            return gen.next()
        old_shouldAutoDeal = self.table.shouldAutoDeal
        self.table.shouldAutoDeal = shouldAutoDealNew

        def beginTurnNew():
            self.assertTrue(False, "beginTurn should not be called")
        old_beginTurn = self.table.beginTurn
        self.table.beginTurn = beginTurnNew

        self.createPlayer(1)
        self.createPlayer(2)
        self.table.scheduleAutoDeal()
        d = defer.Deferred()
        reactor.callLater(2, d.callback, True)
        return d

    # -------------------------------------------------------------------
    def test06_duplicate_buyin(self):
        """ Buy in requested twice for a given player """
        self.createPlayer(1)
        client = self.clients[1]
        self.assertEqual(False, self.table.buyInPlayer(client, self.table.game.maxBuyIn()))
    # -------------------------------------------------------------------
    def test08_player_has_trouble_joining(self):
        """Test for when the table is full and a player is trying hard to join"""
        # Do not use serials of 0's here -- pokerengine will hate that. :)
        player = {}
        for ii in [1, 2, 3, 4]:
            player[ii] = self.createPlayer(ii)
        player[5] = self.createPlayer(5, False)

        # people at table aren't obsrevers
        self.assertEqual(False, self.table.isSerialObserver(1))

        # player5 can't sit because the table is full of 1-4...
        self.assertEqual(False, self.table.seatPlayer(player[5], -1))
        # player5 still not an observer
        self.assertEqual(False, self.table.isSerialObserver(5))

        self.assertEqual(True, self.table.joinPlayer(player[5]))
        # player5 now an observer
        self.assertEqual(True, self.table.isSerialObserver(5))
        #  ... but player5 decides to set all sorts of things that she can't
        #      because she's still just an observer.
        self.assertEqual(False, self.table.muckAccept(player[5]))
        self.assertEqual(False, self.table.muckDeny(player[5]))
        self.assertEqual(False, self.table.autoBlindAnte(player[5], True))
        self.assertEqual(False, self.table.buyInPlayer(player[5], 0))
        self.assertEqual(False, self.table.rebuyPlayerRequestNow(5, 30))

        # player5 cannot sit out either because she isn't joined yet.
        self.assertEqual(False, self.table.sitOutPlayer(player[5]))

        # player1 leaves on his own...
        self.assertEqual(True, self.table.leavePlayer(player[1]))

        # ... which allows player5 to finally join legitimately and change
        # her settings.  However, she tries to sit in everyone else's
        # seat, she tries to sit out before getting the seat, rebuy before
        # even buying, and then buys in for nothing, and thus must rebuy

        for p in self.table.game.playersAll():
            self.assertEqual(False, self.table.seatPlayer(player[5], p.seat))

        self.assertEqual(False, self.table.sitPlayer(player[5]))

        self.assertEqual(True, self.table.seatPlayer(player[5], -1))
        self.assertEqual(False, self.table.rebuyPlayerRequestNow(5, 2))

        self.assertEqual(True, self.table.buyInPlayer(player[5], 0))

        # ... but cannot sit down again
        self.assertEqual(False, self.table.seatPlayer(player[5], -1))

        self.assertEqual(None, self.table.muckAccept(player[5]))
        self.assertEqual(None, self.table.muckDeny(player[5]))
        self.assertEqual(None, self.table.autoBlindAnte(player[5], True))

        self.assertEqual(True, self.table.rebuyPlayerRequest(5, self.table.game.buyIn()))
        # finally, player5 tries to join table 2, which isn't permitted since
        # we've set MockService.simultaneous to 1
        self.assertEqual(False, self.table2.joinPlayer(player[5]))
    # -------------------------------------------------------------------
    def test08_2_brokenSeatFactory(self):
        player = self.createPlayer(1, False)
        self.assertEqual(True, self.table.joinPlayer(player))
        self.table.factory.seatPlayer = lambda a, b, c, d=None: False
        self.assertEqual(False, self.table.seatPlayer(player, -1))
    # -------------------------------------------------------------------
    def test08_5_kick(self):
        """Test that kick works correctly"""
        player = self.createPlayer(2)

        self.assertEqual(None, self.table.kickPlayer(2))
        # Test to make sure it's ok if we kick him twice.
        try:
            self.assertEqual(None, self.table.kickPlayer(2))
        except KeyError, ke:
            self.assertEqual(2, ke[0])

        # Special test: player 9's removePlayer always fails
        p = self.createPlayer(9)
        self.assertEquals(None, self.table.kickPlayer(9))
    # -------------------------------------------------------------------
    def test08_7_sitout(self):
        """Test that sitOut works correctly"""
        player = self.createPlayer(4)

        # player4 sits out but tries it twice.
        self.assertEqual(True, self.table.sitOutPlayer(player))
        self.assertEqual(True, self.table.sitOutPlayer(player))
    # -------------------------------------------------------------------
    def test08_8_buyinOverMax(self):
        """Test that buyins over the maximum are refused"""
        player = self.createPlayer(1)

        self.assertEqual(False, self.table.rebuyPlayerRequest(1, 1))
        self.assertEqual(False, self.table.rebuyPlayerRequest(1, 2))
    # -------------------------------------------------------------------
    def test09_list_players(self):
        """Test to make sure the list of players given by pokertable is right"""
        d = {}
        for ii in [1, 2, 3, 4]:
            d['Player%d' % ii] = self.createPlayer(ii)
        for x in self.table.listPlayers():
            del d[x[0]]
        self.assertEqual({}, d)
    # -------------------------------------------------------------------
    def test10_info_and_chat(self):
        """Test player discussions and info"""
        
        def chatCatch(packet):
            self.assertEqual(packet.serial, 1)
            self.assertEqual(packet.message.strip(), "Hi, I am the One.")
        
        dl = []
        for serial in [1, 2, 3, 4]:
            p = self.createPlayer(serial)
            x = p.waitFor(PACKET_POKER_CHAT)
            x.addCallback(chatCatch)
            dl.append(x)
        
        self.table.chatPlayer(self.clients[1], "Hi, I am the One.")
        self.assertEquals(1, self.service.chat_messages[0][0])
        self.assertEquals(table1ID, self.service.chat_messages[0][1])
        self.assertEquals("Hi, I am the One.", self.service.chat_messages[0][2])
        
        return defer.DeferredList(dl)
    # -------------------------------------------------------------------
    def test11_packet(self):
        """Test toPacket"""
        table = self.table

        packet = self.table.toPacket()
        assert packet.id == table.game.id
        assert packet.name == table.game.name
        assert packet.variant == table.game.variant
        assert packet.betting_structure == table.game.betting_structure
        assert packet.seats == table.game.max_players
        assert packet.players == table.game.allCount()
        assert packet.average_pot == table.game.stats['hands_per_hour']
        assert packet.hands_per_hour == table.game.stats['average_pot']
        assert packet.percent_flop == table.game.stats['percent_flop']
        assert packet.player_timeout == table.playerTimeout
        assert packet.muck_timeout == table.muckTimeout
        assert packet.observers == len(table.observers)
        assert packet.waiting == len(table.waiting)
        assert packet.skin == table.skin
        assert packet.currency_serial == table.currency_serial
        assert packet.tourney_serial == (table.tourney.serial if table.tourney else 0)

    # -------------------------------------------------------------------
    def test12_everyone_timeout(self):
        """Test if all players fall through timeout"""
        player = {}
        for ii in [1, 2, 3, 4]:
            player[ii] = self.createPlayer(ii)
        self.table.update()

        return defer.DeferredList((player[1].waitFor(PACKET_POKER_TIMEOUT_NOTICE),
                                   player[2].waitFor(PACKET_POKER_TIMEOUT_NOTICE),
                                   player[3].waitFor(PACKET_POKER_TIMEOUT_NOTICE),
                                   player[4].waitFor(PACKET_POKER_TIMEOUT_NOTICE)))
    # -------------------------------------------------------------------
    def test13_disconnect(self):
        """Test a disconnected player"""
        p1 = self.createPlayer(1, clientClass=MockClientWithTableDict)
        p9 = self.createPlayer(9, clientClass=MockClientWithTableDict)
        self.table.disconnectPlayer(p1)
        self.table.disconnectPlayer(p9)
    # -------------------------------------------------------------------
    def test14_closed_games(self):
        """Do typical operations act as expected when the game is closed?"""
        player = {}
        for ii in [1, 2, 3, 4]:
            player[ii] = self.createPlayer(ii)
        self.table.game.close()
        self.table.quitPlayer(player[1])

        # Leaving a closed table generates an error.  player[2] is going
        # to leave, we wait for the error packet to come back, and make
        # sure that they other_type indicates it's a response to our leave
        # request.
        deferredLeaveErrorWait = player[2].waitFor(PACKET_POKER_ERROR)
        def checkReturnPacket(packet):
            self.assertEqual(PACKET_POKER_PLAYER_LEAVE, packet.other_type)
            self.failUnlessSubstring("annot leave", packet.message)
        deferredLeaveErrorWait.addCallback(checkReturnPacket)

        self.table.leavePlayer(player[2])
        return deferredLeaveErrorWait
    # -------------------------------------------------------------------
    def test16_autoMuckTimeoutPolicy(self):
        """Make sure other timeout policies function properly"""
        player = self.createPlayer(1)
        player2 = self.createPlayer(2)
        # Sit out policy is the default
        self.assertTrue(self.table.isOpen())
        self.table.game.close()

        expectPlayerAutoFold = player2.waitFor(PACKET_POKER_AUTO_FOLD)
        def checkReturnPacket(packet):
            # Don't assert which serial we get here, as it could be from
            # either player
            self.assertEqual(packet.game_id, self.table1_value)
            return packet
        def openGameAgain(packet):
            self.table.game.open()
        expectPlayerAutoFold.addCallback(checkReturnPacket)
        expectPlayerAutoFold.addCallback(openGameAgain)
        
        log_history.reset()
        self.table.update()
        self.assertEquals(log_history.get_all(), ['AutodealCheck scheduled in 0.000000 seconds'])

        return expectPlayerAutoFold
    # -------------------------------------------------------------------
    def test18_handReplay(self):
        """Test replay of hand from pokertable"""
        player1 = self.createPlayer(1)

        # First try a hand that doesn't exist
        self.assertEqual(None, self.table.handReplay(player1, 0))

        myHandId = randint(777, 79825)
        def checkHandSerial(packet):
            self.assertEqual(packet.hand_serial, myHandId)
        def checkAmount(amount, value):
            self.assertEqual(amount, value)
        def checkAnteAmount(packet):
            checkAmount(packet.amount, 111)
        def checkBlindAmount(packet):
            checkAmount(packet.amount, 222)
        def checkCallAmount(packet):
            checkAmount(packet.amount, 411)
        def checkRaiseAmount(packet):
            checkAmount(packet.amount, 888)
        def checkRebuyAmount(packet):
            checkAmount(packet.amount, 9999)
        def checkCanceledAmount(packet):
            checkAmount(packet.amount, 10)
        def checkRakeAmount(packet):
            self.assertEqual(packet.value, 7)
        def checkPosition(packet):
            self.assertEqual(packet.position, 1)
        def checkBlindRequest(packet):
            self.assertEqual(packet.state, "big_and_dead")
            checkBlindAmount(packet)
        def checkPlayerMoney(packet):
            self.assertEqual(True, packet.serial == 1 or packet.serial == 2)
            if packet.serial == 1:
                self.assertEqual(packet.amount, 7890)
            else:
                self.assertEqual(packet.amount, 1234)
        def checkPlayerCards(packet):
            self.assertEqual(True, packet.serial == 1 or packet.serial == 2)
            if packet.serial == 1:
                self.assertEqual(packet.cards, [23, 47])
            else:
                self.assertEqual(packet.cards, [11, 37])
        def checkMuckSerials(packet):
            self.assertEqual(packet.muckable_serials, (1, 2))

        # To get coverage of a player who isn't joined to the table requesting.
        player2 = self.createPlayer(2, False)

        player1.reasonExpected = "HandReplay"
        player2.reasonExpected = "HandReplay"
        for player in (player1, player2):
            self.table.handReplay(player, myHandId)
            checkHandSerial(player.lookForPacket(PACKET_POKER_START))
            checkPlayerCards(player.lookForPacket(PACKET_POKER_PLAYER_CARDS))
            checkPlayerCards(player.lookForPacket(PACKET_POKER_PLAYER_CARDS))
            checkPosition(player.lookForPacket(PACKET_POKER_POSITION))
            checkBlindRequest(player.lookForPacket(PACKET_POKER_BLIND_REQUEST))
            checkBlindAmount(player.lookForPacket(PACKET_POKER_BLIND))
            checkAnteAmount(player.lookForPacket(PACKET_POKER_ANTE_REQUEST))
            checkAnteAmount(player.lookForPacket(PACKET_POKER_ANTE))
            checkRebuyAmount(player.lookForPacket(PACKET_POKER_REBUY))
            player.lookForPacket(PACKET_POKER_CALL)
            player.lookForPacket(PACKET_POKER_CHECK)
            player.lookForPacket(PACKET_POKER_FOLD)
            checkRaiseAmount(player.lookForPacket(PACKET_POKER_RAISE))
            checkCanceledAmount(player.lookForPacket(PACKET_POKER_CANCELED))
            checkRakeAmount(player.lookForPacket(PACKET_POKER_RAKE))
            player.lookForPacket(PACKET_POKER_SIT_OUT)
            checkMuckSerials(player.lookForPacket(PACKET_POKER_MUCK_REQUEST))
            checkRebuyAmount(player.lookForPacket(PACKET_POKER_REBUY))
    # -------------------------------------------------------------------
    def test19_avatar_collection_empty(self):
        """Test replay of hand from pokertable"""
        self.assertEqual("MockServicePlayerName1", self.table.getName(1))
        d = self.table.getPlayerInfo(1)
        self.failUnlessSubstring("MockServicePlayerInfo", d.name)
    # -------------------------------------------------------------------
    def test20_quitting(self):
        p = self.createPlayer(1)
        self.assertEquals(True, self.table.quitPlayer(p))
        p = self.createPlayer(2, False, clientClass=MockClientWithTableDict)
        self.assertEqual(True, self.table.joinPlayer(p))
        p.tables[self.table.game.id] = self.table
        self.assertEquals(True, self.table.quitPlayer(p))
        # Special test: player 9's removePlayer always fails
        p = self.createPlayer(9)
        self.assertEquals(True, self.table.quitPlayer(p))
    # -------------------------------------------------------------------
    def test20_1_brokenLeaving(self):
        p = self.createPlayer(1)
        self.assertEquals(True, self.table.leavePlayer(p))
        # Special test: player 9's removePlayer always fails
        p = self.createPlayer(9)
        self.assertEquals(True, self.table.leavePlayer(p))
    # -------------------------------------------------------------------
    def test21_syncDatabase(self):
        """Test syncing the Database back to the MockService"""
        player = {}
        for ii in [1, 2, 3, 4]:
            player[ii] = self.createPlayer(ii)
            self.table.readyToPlay(ii)
        self.service.testObject = self
        self.table.game.turn_history = self.service.loadHand(randint(777, 8975))
        log_history.reset()
        self.table.update()
        
        return player[3].waitFor(PACKET_POKER_TIMEOUT_NOTICE)
    # -------------------------------------------------------------------
    def test22_possibleObserverLoggedIn(self):
        """Test possibleObserverLoggedIn"""
        p = self.createPlayer(1)
        self.table.disconnectPlayer(p)
        p2 = self.createPlayer(2)
        # Player 1 is already at the table, so this should be meaningless:
        self.table.possibleObserverLoggedIn(p)
        # Player 2's object has been "lost", s owe created
        p2_reconnected = self.createPlayer(3, getReadyToPlay=False) 
        self.table.joinPlayer(p2_reconnected)
        self.table.possibleObserverLoggedIn(p2_reconnected)
    # -------------------------------------------------------------------
    def test23_broadcastingPlayerCards(self):
        """Test to make sure PokerPlayerCards are broadcasted correctly.  This
        test is not particularly good, in my view, because it was written
        to target certain lines in private2public directly and may not
        actually be an adequate test of actual functionality."""
        p = self.createPlayer(1)
        p2 = self.createPlayer(2)
        c1 = PokerCards([ 'As', 'Ah' ])
        c1.allHidden()
        self.table.game.getPlayer(2).hand.set(c1)
        self.table.broadcast([ PacketPokerPlayerCards(game_id = self.table.game.id, serial = 2,
                                                      cards = self.table.game.getPlayer(2).hand.toRawList())])
        def checkReturnPacketBySerial(packet, serial):
            self.assertEqual(packet.serial, 2)
            if serial == 2:
                hand_expected = [243, 204]
            else:
                hand_expected = [255, 255]
            self.assertEqual(packet.cards, hand_expected)
            self.assertEqual(packet.game_id, self.table1_value)
        
        checkReturnPacketBySerial(p.lookForPacket(PACKET_POKER_PLAYER_CARDS), 1)
        checkReturnPacketBySerial(p2.lookForPacket(PACKET_POKER_PLAYER_CARDS), 2)
    # -------------------------------------------------------------------
    def test24_treeFallingInWoodsWithNoPlayerToHearIt(self):
        """Test a broadcast message that no one is here to hear"""
        self.assertEqual(False, self.table.broadcastMessage(PacketPokerGameMessage, "Tommy, can you hear me?"))
    # -------------------------------------------------------------------
    def test25_buyingInWhilePlaying(self):
        """Test if all players fall through timeout"""
        player = {}
        for ii in [1, 2, 3, 4]:
            player[ii] = self.createPlayer(ii)

        def postBlindCallback(packet):
            player[3].sendPacket(PacketPokerBlind())
            self.table.buyInPlayer(player[3], 10)
        defPlayer3Blind = player[3].waitFor(PACKET_POKER_BLIND_REQUEST)
        
        defPlayer3Blind.addCallback(postBlindCallback)

        self.table.update()
        return defPlayer3Blind
    # -------------------------------------------------------------------
    def test26_wrongPlayerUpdateTimes(self):
        """Test if playerUpdateTimers get called with the wrong serial"""
        p = self.createPlayer(1)
        p = self.createPlayer(2)
        self.assertEqual(None, self.table.playerWarningTimer(2))
        self.assertEqual(None, self.table.playerTimeoutTimer(2))
    # -------------------------------------------------------------------
    def test27_buyinFailures(self):
        p9 = self.createPlayer(9, False)
        assert self.table.joinPlayer(p9)
        assert self.table.seatPlayer(p9, -1)
        assert self.table.buyInPlayer(p9, 10)

        p9.money = 0
        assert not self.table.rebuyPlayerRequest(9, 50)

        p1 = self.createPlayer(1)
        self.table.game.rebuy = lambda a, b: False

        p1.money = 50
        assert not self.table.rebuyPlayerRequest(1, 0)
    # -------------------------------------------------------------------
    def checkFailedJoinDueToMax(self, player):
        self.assertEqual(False, self.table.isJoined(player))
        self.assertEquals(log_history.get_all(), [
            'joinPlayer: %d cannot join game %d because the server is full' % (
                player.serial,
                self.table.game.id
            ),
            'sendPacket: PacketPokerError(53) serial: %d ' \
            'game_id: %d message: \'This server has too many seated players and ' \
            'observers.\' code: 1 other_type: 71' % (
                player.serial,
                self.table.game.id
            )
        ])
        self.assertEquals(len(player.packets), 1)
        p = player.packets[0]
        self.assertEquals(p.type, PACKET_POKER_ERROR)
        self.assertEquals(p.serial, player.serial)
        self.assertEquals(p.game_id, self.table.game.id)
        self.assertEquals(p.message, "This server has too many seated players and observers.")
        self.assertEquals(p.code, PacketPokerTableJoin.FULL)
        self.assertEquals(p.other_type, PACKET_POKER_TABLE_JOIN)
        player.packets = []
    # -------------------------------------------------------------------
    def doJoinAndFailDueToMax(self, player):
        """Helper method used to check to for a join failed due to the
        maximum value."""
        log_history.reset()
        self.table.joinPlayer(player)
        self.checkFailedJoinDueToMax(player)
    # -------------------------------------------------------------------
    def test28_tooManyPlayers(self):
        """Generate so many players, trying to join tables, such that we
        get too many.  To force this to happen, we decrease the number of
        permitted players to be very low."""
        log_history.reset()
        self.table.factory.joined_max = 3
        self.assertEquals(self.table.factory.joined_count, 0)
        players = {}
        for ii in [ 1, 2, 3, 4 ]:
            players[ii] = self.createPlayer(ii, getReadyToPlay = False)
            self.assertEquals(self.table.factory.joined_count, 0)

        for ii in [ 1, 2, 3 ]:
            self.table.joinPlayer(players[ii])
            self.assertEqual(True, self.table.isJoined(players[ii]))
            self.assertEqual(players[ii].packets, [])
            self.assertEquals(self.table.factory.joined_count, ii)
        self.assertEquals(log_history.get_all(), [])
        self.doJoinAndFailDueToMax(players[4])
        self.assertEquals(self.table.factory.joined_count, 3)
    # -------------------------------------------------------------------
    def test29_leavingDoesNotDecreaseCount(self):
        """Players who leave do not actually cease being observers, and
        therefore do not decrease max join count"""
        log_history.reset()
        self.table.factory.joined_max = 3
        self.assertEquals(self.table.factory.joined_count, 0)
        players = {}
        for ii in [ 1, 2, 3, 4 ]:
            players[ii] = self.createPlayer(ii, getReadyToPlay = False)
            self.assertEquals(self.table.factory.joined_count, 0)

        for ii in [ 1, 2, 3 ]:
            self.table.joinPlayer(players[ii])
            self.assertEqual(True, self.table.isJoined(players[ii]))
            self.assertEqual(players[ii].packets, [])
            self.assertEquals(self.table.factory.joined_count, ii)
        self.assertEquals(log_history.get_all(), [])
        self.assertEquals(True, self.table.leavePlayer(players[1]))

        self.doJoinAndFailDueToMax(players[4])
        self.assertEquals(self.table.factory.joined_count, 3)
    # -------------------------------------------------------------------
    def test30_justEnoughPlayers(self):
        """Tests situation where players truely are gone from the table
        and are no longer observers either, thus allowing more players to
        be conntected."""
        log_history.reset()
        self.table.factory.joined_max = 3
        self.assertEquals(self.table.factory.joined_count, 0)
        players = {}
        for ii in [ 1, 2, 3 ]:
            players[ii] = self.createPlayer(ii, getReadyToPlay = True)
            self.assertEqual(True, self.table.isJoined(players[ii]))
            self.assertEquals(self.table.factory.joined_count, ii)
        messages = log_history.get_all()
        messages_string = "\n".join(messages)
        self.failUnlessSubstring('player 1 gets seat 1', messages_string)
        self.failUnlessSubstring('player 2 gets seat 6', messages_string)
        self.failUnlessSubstring('player 3 gets seat 3', messages_string)
        log_history.reset()

        for ii in [ 4, 5, 6 ]:
            players[ii] = self.createPlayer(ii, getReadyToPlay = False)
            self.assertEquals(self.table.factory.joined_count, 3)
        self.assertEquals(log_history.get_all(), [])

        # leavePlayer turns an actual player into an observer, so they are still
        #  connected.  player 4 should still be unable to join.
        self.assertEquals(True, self.table.leavePlayer(players[1]))
        self.assertEquals(self.table.factory.joined_count, 3)
        self.doJoinAndFailDueToMax(players[4])
        self.assertEquals(self.table.factory.joined_count, 3)
        log_history.reset()

        self.assertEquals(True, self.table.quitPlayer(players[2]))
        log_history.search('[Server][PokerGame %d] removing player %d from game'
                      % (self.table.game.id, players[2].serial))
        log_history.reset()
        self.assertEquals(self.table.factory.joined_count, 2)

        self.table.joinPlayer(players[4])
        self.assertEqual(True, self.table.isJoined(players[4]))
        self.assertEquals(log_history.get_all(), [])
        self.assertEquals(self.table.factory.joined_count, 3)

        self.assertEquals(None, self.table.kickPlayer(players[3].serial))
        log_history.search('[Server][PokerGame %d] removing player %d from game'
                      % (self.table.game.id, players[3].serial))
        self.assertEquals(self.table.factory.joined_count, 3)

        self.doJoinAndFailDueToMax(players[5])
        log_history.reset()
        self.assertEquals(self.table.factory.joined_count, 3)

        self.assertEquals(True, self.table.disconnectPlayer(players[3]))
        log_history.search('[Server][PokerGame %d] removing player %d from game'
                      % (self.table.game.id, players[3].serial))
        log_history.reset()
        self.assertEquals(self.table.factory.joined_count, 2)

        self.table.joinPlayer(players[5])
        self.assertEqual(True, self.table.isJoined(players[5]))
        self.assertEquals(log_history.get_all(), [])
        self.assertEquals(self.table.factory.joined_count, 3)

        self.doJoinAndFailDueToMax(players[6])
        log_history.reset()
        self.assertEquals(self.table.factory.joined_count, 3)
    # -------------------------------------------------------------------
    def test31_kickPlayerForMissingTooManyBlinds(self):
        """test31_kickPlayerForMissingTooManyBlinds
        Players who pass or equal the max_missed_round count are
        automatically kicked from the table and turned into observers.
        This happens via the update function's call of
        cashGame_kickPlayerSittingOutTooLong().  That function searches in
        the history for a 'finish' event (meaning the hand is done) and
        then kicks the player afer that.  This test sets up that situatoin
        and makes sure the player gets kicked."""
        player = {}
        for ii in [1, 2, 3, 4]:
            player[ii] = self.createPlayer(ii)
            self.assertEquals(ii in self.service.players, True)

        self.table.game.serial2player[4].missed_big_blind_count = 1000
        self.table.game.turn_history = self.service.loadHand(6352,
                                         [('leave', [(1, 2), (2, 7)])])

        # Table starts with no observers before our update

        self.assertEquals(len(self.table.observers), 0)
        log_history.reset()
        self.table.update()
        messages = log_history.get_all()
        self.assertTrue(log_history.search('removing player 4 from game'))
        self.assertTrue(log_history.search(
            "broadcast[1, 2, 3] PacketPokerPlayerLeave(81) "
            "serial: 4 game_id: %d seat: 8" % (self.table1_value,)
        ))
        self.assertEqual(messages.count(
            "sendPacket: PacketPokerPlayerLeave(81) "
            "serial: 4 game_id: %d seat: 8" % (self.table1_value,)
        ), 4)
        
        for ii in [1, 2, 3, 4]:
            # Our service's leavePlayer() should have been called for 4,
            # the rest should still be there
            self.assertEquals(ii in self.service.players, ii != 4)
            foundCount = 0
            for pp in player[ii].packets:
                if pp.type == PACKET_POKER_PLAYER_LEAVE:
                    foundCount += 1
                    self.assertEquals(pp.serial, 4)
                    self.assertEquals(pp.game_id, self.table1_value)
                    self.assertEquals(pp.seat, 8)
            self.assertEquals(foundCount, 1)
        # Table should now have one observer, 4
        self.assertEquals(len(self.table.observers), 1)
        self.assertEquals(self.table.observers[0].serial, 4)
        return player[1].waitFor(PACKET_POKER_TIMEOUT_NOTICE)
    # -------------------------------------------------------------------
    def test32_seatPlayerUpdateTableStats(self):
        player = self.createPlayer(1, False)
        def updateTableStats(game, observers, waiting):
            updateTableStats.called = True
        updateTableStats.called = False
        self.table.factory.updateTableStats = updateTableStats
        self.assertEquals(True, self.table.joinPlayer(player))
        self.assertEquals(True, self.table.seatPlayer(player, 1))
        self.assertEquals(True, updateTableStats.called)
    # -------------------------------------------------------------------
    def test33_leavePlayerUpdateTableStats(self):
        player = self.createPlayer(1)
        def updateTableStats(game, observers, waiting):
            updateTableStats.called = True
        updateTableStats.called = False
        self.table.factory.updateTableStats = updateTableStats
        self.table.game.is_open = True
        self.table.leavePlayer(player)
        self.assertEquals(True, updateTableStats.called)
    # -------------------------------------------------------------------
    def test34_leavePlayerDelayedNoUpdateTableStats(self):
        player = self.createPlayer(1)
        def updateTableStats(game, observers, waiting):
            updateTableStats.called = True
        updateTableStats.called = False
        self.table.factory.updateTableStats = updateTableStats
        self.table.game.is_open = False
        self.table.leavePlayer(player)
        self.assertEquals(False, updateTableStats.called)
    # -------------------------------------------------------------------
    def test35_quitPlayerUpdateTableStats(self):
        player = self.createPlayer(1)
        def updateTableStats(game, observers, waiting):
            updateTableStats.called = True
        updateTableStats.called = False
        self.table.factory.updateTableStats = updateTableStats
        self.table.game.is_open = True
        self.table.quitPlayer(player)
        self.assertEquals(True, updateTableStats.called)
    # -------------------------------------------------------------------
    def test36_quitPlayerDelayedNoUpdateTableStats(self):
        player = self.createPlayer(1)
        def updateTableStats(game, observers, waiting):
            updateTableStats.called = True
        updateTableStats.called = False
        self.table.factory.updateTableStats = updateTableStats
        self.table.game.is_open = False
        self.table.quitPlayer(player)
        self.assertEquals(False, updateTableStats.called)
    # -------------------------------------------------------------------
    def test37_disconnectPlayerUpdateTableStats(self):
        player = self.createPlayer(1)
        def updateTableStats(game, observers, waiting):
            updateTableStats.called = True
        updateTableStats.called = False
        self.table.factory.updateTableStats = updateTableStats
        self.table.game.is_open = True
        self.table.disconnectPlayer(player)
        self.assertEquals(True, updateTableStats.called)
    # -------------------------------------------------------------------
    def test38_disconnectPlayerDelayedNoUpdateTableStats(self):
        player = self.createPlayer(1)
        def updateTableStats(game, observers, waiting):
            updateTableStats.called = True
        updateTableStats.called = False
        self.table.factory.updateTableStats = updateTableStats
        self.table.game.is_open = False
        self.table.disconnectPlayer(player)
        self.assertEquals(False, updateTableStats.called)
    # -------------------------------------------------------------------
    def test39_kickPlayerUpdateTableStats(self):
        player = self.createPlayer(1)
        def updateTableStats(game, observers, waiting):
            updateTableStats.called = True
        updateTableStats.called = False
        self.table.factory.updateTableStats = updateTableStats
        self.table.game.is_open = True
        self.table.kickPlayer(1)
        self.assertEquals(True, updateTableStats.called)
    # -------------------------------------------------------------------
    def test40_destroy_table(self):
        """Test table destruction"""
        p1 = self.createPlayer(1, clientClass=MockClientWithTableDict)
        d = p1.waitFor(PACKET_POKER_TABLE_DESTROY)
        self.table.destroy()
        # Make sure we can't update once table is destroyed.
        self.assertEquals("not valid", self.table.update())
        return d
    # -------------------------------------------------------------------
    def test40_destroy_table_with_observers(self):
        """Test table destruction with observers at the table"""
        p1 = self.createPlayer(1, clientClass=MockClientWithTableDict)
        self.table.seated2observer(p1)
        d = p1.waitFor(PACKET_POKER_TABLE_DESTROY)
        self.table.destroy()
        # Make sure we can't update once table is destroyed.
        self.assertEquals("not valid", self.table.update())
        return d
    # -------------------------------------------------------------------
    def test41_update_exception(self):
        """Test if exception caught in update and history reduced"""
        self.table.history_index = -1
        def failure(history_tail):
            raise Exception("FAIL")
        self.table.updateTimers = failure
        exception_occurred = False
        try:
            self.table.update()
        except Exception, e:
            exception_occurred = True
            self.assertEquals("FAIL", str(e))
        self.assertEquals(0, self.table.history_index)
        self.assertEquals(True, exception_occurred)
    # -------------------------------------------------------------------
    def test42_update_recursion(self):
        """Test if update is protected against recursion"""
        self.table.prot = False
        def recurse(dummy):
            self.table.prot = True
            self.assertEquals("recurse", self.table.update())
        self.table.updateTimers = recurse
        self.assertEquals("ok", self.table.update())
        self.assertEquals(True, log_history.search('unexpected recursion'))
        self.assertEquals(True, self.table.prot)
    # -------------------------------------------------------------------
    def test43_gameStateIsMuckonAutoDealSched(self):
        """If game state is muck when autodeal tries to schedule, it should fail"""
        from pokerengine.pokergame import GAME_STATE_MUCK
        player = {}
        for ii in [1, 2, 3, 4]:
            player[ii] = self.createPlayer(ii)

        log_history.reset()
        self.table.game.state = GAME_STATE_MUCK
        for ii in [1, 2, 3, 4]:
            player[ii].packets= []

        self.table.scheduleAutoDeal()

        # No packets should be received if we tried to autodeal in
        # GAME_STATE_MUCK
        for ii in [1, 2, 3, 4]:
            self.assertEquals(player[ii].packets, [])

        log_history.search("Not autodealing %d because game is in muck state" % self.table.game.id)
    # -------------------------------------------------------------------
    def test44_muckTimeoutTimer_hollowedOutGameWithMuckableSerials(self):
        from pokerengine.pokergame import GAME_STATE_MUCK, GAME_STATE_END
        class MockGame():
            def __init__(mgSelf):
                mgSelf.muckable_serials = [ 1, 2 ]
                mgSelf.mucked = {}
                mgSelf.id = 77701
                mgSelf.state = GAME_STATE_MUCK
                mgSelf.hand_serial = 77701
                mgSelf.serial2player = {}
            def muck(mgSelf, serial, want_to_muck = False):
                mgSelf.mucked[serial] = want_to_muck

            # Rest MockGame methods below are dummy methods needed when
            # self.table.update() gets called.
            def historyGet(mgSelf): return []
            def isRunning(mgSelf): return False
            def potAndBetsAmount(mgSelf): return 0
            def historyCanBeReduced(mgSelf): False
            def betLimits(mgSelf): return (0, 0)
            def getChipUnit(mgSelf): return 1
            def roundCap(mgSelf): return 1
            def isEndOrMuck(mgSelf): return mgSelf.state in (GAME_STATE_MUCK, GAME_STATE_END)
            def playersAll(mgSelf): return mgSelf.serial2player.values()

        self.table.timer_info["muckTimeout"] = None
        origGame = self.table.game
        self.table.game = MockGame()
        log_history.reset()

        self.table.muckTimeoutTimer()

        self.assertEquals(len(self.table.game.mucked.keys()), 2)
        for ii in [ 1, 2 ]:
            self.failUnless(self.table.game.mucked[ii], "Serial %d should be mucked" % ii)
        self.assertEquals(log_history.get_all()[0], 'muck timed out')
        self.table.game = origGame
    # -------------------------------------------------------------------
    def test45_cancelMuckTimer_hollowedOutTimer(self):
        class AMockTime(): # Spot the ST:TOS reference. :-) -- bkuhn
            def __init__(amtSelf):
                amtSelf.cancelCalledCount = 0
                amtSelf.activeCalledCount = 0
            def active(amtSelf):
                amtSelf.activeCalledCount += 1
                return True
            def cancel(amtSelf):
                amtSelf.cancelCalledCount += 1
        saveTimerInfo = self.table.timer_info

        aMockTimer = AMockTime()
        self.table.timer_info = { 'muckTimeout' : aMockTimer }

        log_history.reset()
        self.table.cancelMuckTimer()

        self.assertEquals(self.table.timer_info['muckTimeout'], None)
        self.assertEquals(aMockTimer.cancelCalledCount, 1)
        self.assertEquals(aMockTimer.activeCalledCount, 1)
        self.assertEquals(log_history.get_all(), [])

        self.table.timer_info = saveTimerInfo
    # -------------------------------------------------------------------
    def test46_updatePlayerTimers_hollowedOutGameAndMockedTableVals(self):
        from pokerengine.pokergame import GAME_STATE_MUCK
        class MockGame():
            def __init__(mgSelf):
                mgSelf.muckable_serials = [ 1, 2 ]
                mgSelf.mucked = {}
                mgSelf.id = 77701
                mgSelf.state = GAME_STATE_MUCK
                hand_serial = 77701
            def isRunning(mgSelf): return True
            def getSerialInPosition(mgSelf): return 664
            def historyGet(mgSelf): return [ "" ]

        self.table.game = MockGame() 
        self.table.playerTimeout = 100
        self.table.history_index = -1
        deferredMustBeCalledBackForSuccess = defer.Deferred()
        def myPlayerTimeout(serial):
            self.assertEquals(self.tableSave.timer_info["playerTimeoutSerial"], serial)
            self.assertEquals(serial, 664)
            deferredMustBeCalledBackForSuccess.callback(True)
            self.assertEquals(log_history.get_all(), [])

        self.table.playerWarningTimer = myPlayerTimeout
        def failedToCancelTimeout():
            self.fail("existing playerTimeout was not replaced as expected")

        self.table.timer_info = { 
            'playerTimeout': reactor.callLater(20, failedToCancelTimeout), 
            'playerTimeoutSerial': 229 
        }
        # Note: serial is diff from one in position
        log_history.reset()
        self.table.updatePlayerTimers()

        self.tableSave = self.table

        return deferredMustBeCalledBackForSuccess
    # -------------------------------------------------------------------
    def test48_muckTimeoutTimerShouldEmptyMuckableSerials(self):
        """
        See https://gna.org/bugs/?13898
        """
        from pokerengine.pokergame import GAME_STATE_MUCK

        self.table.timer_info["muckTimeout"] = None
        log_history.reset()

        self.createPlayer(1)
        self.createPlayer(2)

        self.table.beginTurn()

        self.table.game.state = GAME_STATE_MUCK
        self.table.game.muckable_serials = [1,2]
        self.table.syncDatabase = lambda history: None
        self.table.muckTimeoutTimer()
        self.assertEquals([], self.table.game.muckable_serials)

    def test49_playersWillingToPlay(self):
        from pokerengine.pokergame import GAME_STATE_MUCK, GAME_STATE_BLIND_ANTE

        self.table.timer_info["muckTimeout"] = None
        log_history.reset()

        p1 = self.createPlayer(1)
        p2 = self.createPlayer(2)
        
        self.table.autoRebuy(p1.serial, 2)
        self.table.game.sitOut(p1.serial)
        self.table.autodeal = True

        self.assertFalse(self.table.shouldAutoDeal())

        # print "serialsWillingToPlay", self.table.serialsWillingToPlay()
        # print "shouldAutoDeal      ", self.table.shouldAutoDeal()
        # print "rebuyPlayersOnes    ", self.table.rebuyPlayersOnes()
        # print "players all         ", [p.serial for p in self.table.game.playersAll() if (p.auto_refill or p.auto_rebuy)]
        # print "game serials sit    ", self.table.game.serialsSit()
        # self.table.beginTurn()
        # self.assertEquals(self.table.game.state, GAME_STATE_BLIND_ANTE)
        # assert False


        # self.table.game.state = GAME_STATE_MUCK
        # self.table.game.muckable_serials = [1,2]
        # self.table.syncDatabase = lambda history: None
        # self.table.muckTimeoutTimer()
        # self.assertEquals([], self.table.game.muckable_serials)
# -------------------------------------------------------------------

# I seriously considered not having *all* the same tests run with
# predifined decks because it was not needed to get coverage.  A simple
# setup test would have worked.  However, I think it's good leaving it
# this way because if predifined decks are later used extensively, we
# would want all the tests to run and when additional use of predefined
# decks is added.  -- bkuhn, 2008-01-21

# I later decided to mix together the tests for predefined decks with
# tests for autodeal turned off; that's why so many tests are replaced.

class PokerTableTestCaseWithPredefinedDecksAndNoAutoDeal(PokerTableTestCase):
    def setUp(self, settingsXmlStr=settings_stripped_deck_no_autodeal_xml, ServiceClass = MockService):
        PokerTableTestCase.setUp(self, settingsXmlStr, ServiceClass)

    # -------------------------------------------------------------------
    def test01_8_testClientsBogusPokerProcessingHand(self):
        """Test specific situation in autodeal when poker clients send a
        Processing Hand before a Ready To Play: not needed when autodeal is off"""
        pass
    # -------------------------------------------------------------------
    def test02_autodeal_check(self):
        self.createPlayer(1)
        self.table.processingHand(1)
        self.table.game_delay["delay"] = 2
        self.table.game_delay["start"] = testclock._seconds_value
        self.createPlayer(2)
        self.assertEqual(False, self.table.scheduleAutoDeal())
    # -------------------------------------------------------------------
    def test12_everyone_timeout(self):
        """Test if all players fall through timeout"""
        player = {}
        for ii in [1, 2, 3, 4]:
            player[ii] = self.createPlayer(ii)
        self.table.cancelDealTimeout()
        self.table.beginTurn()
        self.table.update()

        return defer.DeferredList([
           player[1].waitFor(PACKET_POKER_TIMEOUT_NOTICE),
           player[2].waitFor(PACKET_POKER_TIMEOUT_NOTICE),
           player[3].waitFor(PACKET_POKER_TIMEOUT_NOTICE),
           player[4].waitFor(PACKET_POKER_TIMEOUT_NOTICE)
        ])
    # -------------------------------------------------------------------
    def test16_autoMuckTimeoutPolicy(self):
        """Make sure other timeout policies function properly"""
        player = self.createPlayer(1)
        player2 = self.createPlayer(2)
        # Sit out policy is the default
        self.assertTrue(self.table.isOpen())
        self.table.game.close()

        expectPlayerAutoFold = player2.waitFor(PACKET_POKER_AUTO_FOLD)
        def checkReturnPacket(packet):
            # Don't assert which serial we get here, as it could be from
            # either player
            self.assertEqual(packet.game_id, self.table1_value)
        def openGameAgain(packet):
            self.table.game.open()
            
        expectPlayerAutoFold.addCallback(checkReturnPacket)
        expectPlayerAutoFold.addCallback(openGameAgain)

        self.table.cancelDealTimeout()
        self.table.beginTurn()
        self.table.update()

        return expectPlayerAutoFold
    # -------------------------------------------------------------------
    def test21_syncDatabase(self):
        """Test syncing the Database back to the MockService"""
        player = {}
        for ii in [1, 2, 3, 4]:
            player[ii] = self.createPlayer(ii)
            self.table.readyToPlay(ii)
        self.service.testObject = self
        self.table.game.turn_history = self.service.loadHand(randint(777, 8975))
        self.table.beginTurn()
        self.table.update()
        return player[4].waitFor(PACKET_POKER_TIMEOUT_NOTICE)
    # -------------------------------------------------------------------
    def test25_buyingInWhilePlaying(self):
        """Test if all players fall through timeout"""
        player = {}
        for ii in [1, 2, 3, 4]:
            player[ii] = self.createPlayer(ii)
        self.table.beginTurn()

        def postBlindCallback(packet):
            player[3].sendPacket(PacketPokerBlind(serial = 3, game_id = packet.game_id, amount = packet.amount, dead = packet.dead))
            self.assertEqual(False, self.table.buyInPlayer(player[3], 10))

        defPlayer3Blind = player[3].waitFor(PACKET_POKER_BLIND_REQUEST)
        
        defPlayer3Blind.addCallback(postBlindCallback)

        self.table.update()

        return defPlayer3Blind
    def test27_buyinFailures(self):
        """This test doesn't matter in this subclass"""
        return True
    # -------------------------------------------------------------------
    def test28_joinTwice(self):
        """Player join a second time : packets sent twice"""
        player = self.createPlayer(1)
        self.assertEqual(True, self.table.isJoined(player))
        def join(table, reason = ""):
            player.joined = True
        player.join = join
        self.assertEqual(True, self.table.joinPlayer(player))
        self.failUnless(player.joined)
    # -------------------------------------------------------------------
    def test31_kickPlayerForMissingTooManyBlinds(self):
        """SKIP THIS TEST IN THIS SUBCLASS
        """
        return True
# -------------------------------------------------------------------
# This class tests the same operations as PokerTableTestCase but for tables that
#  are transient.  Note the outcome of various operations are quite different
#  when the table is transient.
class PokerTableTestCaseTransient(PokerTableTestCase):
    def setUp(self, settingsXmlStr=settings_xml, ServiceClass = MockService):
        testclock._seconds_reset()        
        global table1ID
        global table2ID
        table1ID = table1ID + 1
        table2ID += 1
        self.table1_value = table1ID
        self.table2_value = table2ID

        settings = pokernetworkconfig.Config([])
        settings.doc = libxml2.parseMemory(settingsXmlStr, len(settingsXmlStr))
        settings.header = settings.doc.xpathNewContext()
        self.service = ServiceClass(settings)
        class Tournament:            
            name = 'My Old Sit and Go'
            serial = 2
            def isRebuyAllowed(self, serial): 
                return False
            
        self.table = pokertable.PokerTable(self.service, table1ID, { 
            'name': "table1",
            'variant': "holdem",
            'betting_structure': "1-2_20-200_limit",
            'seats': 4,
            'player_timeout' : 6, 
            'muck_timeout' : 1,
            'transient' : True,
            'tourney' : Tournament(),
            'currency_serial': 0
        })
        self.table2 = pokertable.PokerTable(self.service, table2ID, { 
            'name': "table2",
            'variant': "holdem",
            'betting_structure': "1-2_20-200_limit",
            'seats': 4,
            'player_timeout' : 6, 
            'muck_timeout' : 1,
            'transient' : True,
            'tourney' : Tournament(),
            'currency_serial': 0
        })
        self.service.table1 = self.table
        self.service.table2 = self.table2
        self.clients = {}

    def createPlayer(self, serial, getReadyToPlay=True, clientClass=MockClient, table=None):
        if table == None:
            table = self.table
        client = clientClass(serial, self)
        self.clients[serial] = client
        client.reasonExpected = "MockTransientCreatePlayer"
        table.joinPlayer(client, reason="MockTransientCreatePlayer")
        client.reasonExpected = ""
        if getReadyToPlay:
            self.assertEqual(True, table.seatPlayer(client, -1))
            table.sitPlayer(client)
        return client

    # -------------------------------------------------------------------
    def test01_autodeal(self):
        """ Transient tables hand deal has a minimum duration if all players are in auto mode """
        self.createPlayer(1)
        self.createPlayer(2)
        self.table.game_delay["start"] = testclock._seconds_value
        self.table.scheduleAutoDeal()
        return self.clients[2].waitFor(PACKET_POKER_MESSAGE)
    # -------------------------------------------------------------------
    def test02_autodeal_check(self):
        self.createPlayer(1)
        self.table.processingHand(1)
        self.table.game_delay["delay"] = 2
        self.table.game_delay["start"] = testclock._seconds_value
        self.createPlayer(2)
        self.table.scheduleAutoDeal()
        return self.clients[2].waitFor(PACKET_POKER_MESSAGE)
    # -------------------------------------------------------------------
    def test04_autodeal_transient_now(self):
        """ Transient tables hand deal has no minimum duration if all players are in auto mode but the hand lasted more than the required minium """
        self.createPlayer(1)
        self.createPlayer(2)
        self.table.game_delay["start"] = testclock._seconds_value - 300
        self.table.scheduleAutoDeal()
        self.clients[2].raiseIfPacket(PACKET_POKER_MESSAGE)
        return self.clients[2].waitFor(PACKET_POKER_START)
    # -------------------------------------------------------------------
    def test05_autodeal_transient_normal(self):
        """ Transient tables hand deal normaly if at least one player is not in auto mode """
        self.createPlayer(1)
        self.createPlayer(2)
        self.table.scheduleAutoDeal()
        self.clients[2].raiseIfPacket(PACKET_POKER_MESSAGE)
        return self.clients[2].waitFor(PACKET_POKER_START)

    def test08_player_has_trouble_joining(self):
        """Test for when the table is full and a player is trying hard to join"""
        # Do not use serials of 0's here -- pokerengine will hate that. :)
        player = {}
        for ii in [1, 2, 3, 4]:
            player[ii] = self.createPlayer(ii)
        player[5] = self.createPlayer(5, False)

        # player5 can't sit because the table is full of 1-4...
        self.assertEqual(False, self.table.seatPlayer(player[5], -1))

        #  ... but player5 decides to set all sorts of things that she can't
        #      because she's still just an observer.
        self.assertEqual(False, self.table.muckAccept(player[5]))
        self.assertEqual(False, self.table.muckDeny(player[5]))
        self.assertEqual(False, self.table.autoBlindAnte(player[5], True))

        self.assertEqual(False, self.table.rebuyPlayerRequest(5, 30))

        # player5 cannot sit out either because she isn't joined yet.
        self.assertEqual(False, self.table.sitOutPlayer(player[5]))

        # player1 leaves on his own...
        self.assertEqual(True, self.table.leavePlayer(player[1]))

        # ... which allows player5 to finally join legitimately and change
        # her settings.  However, she tries to sit out before getting the
        # seat, rebuy before even buying, and then buys in for nothing,
        # and thus must rebuy

        self.assertEqual(True, self.table.seatPlayer(player[5], -1))
        self.assertEqual(False, self.table.rebuyPlayerRequest(5, 2))

        # this table is transient, so no one can buy in.
        self.assertEqual(False, self.table.buyInPlayer(player[5], 0))

        # I wonder if these should really return True rather than None?  -- bkuhn
        self.assertEqual(None, self.table.muckAccept(player[5]))
        self.assertEqual(None, self.table.muckDeny(player[5]))
        self.assertEqual(None, self.table.autoBlindAnte(player[5], True))

        self.assertEqual(False, self.table.rebuyPlayerRequest(5, self.table.game.maxBuyIn()))

        # player2 tries to rebuy but is already at the max, and besides,
        # in transient mode, this doesn't work anyway

        self.assertEqual(False, self.table.rebuyPlayerRequest(2, 1))
    # -------------------------------------------------------------------
    def test27_buyinFailures(self):
        """This test doesn't matter in this subclass"""
        return True
    # -------------------------------------------------------------------
    def test28_tooManyPlayers(self):
        """Generate so many players, trying to join tables, such that we
        get too many.  To force this to happen, we decrease the number of
        permitted players to be very low.  Note that for transient tables,
        immediate joins are forced, and therefore we get the error
        immediately upon getting ready to play"""
        log_history.reset()
        self.table.factory.joined_max = 3
        self.assertEquals(self.table.factory.joined_count, 0)
        players = {}
        for ii in [ 1, 2, 3 ]:
            players[ii] = self.createPlayer(ii, getReadyToPlay = False)
            self.assertEqual(True, self.table.isJoined(players[ii]))
            self.assertEquals(self.table.factory.joined_count, ii)
        self.assertEquals(self.table.factory.joined_count, 3)
        log_history.reset()
        players[4] = self.createPlayer(4, getReadyToPlay = False)
        self.checkFailedJoinDueToMax(players[4])
        self.assertEquals(self.table.factory.joined_count, 3)
    # -------------------------------------------------------------------
    def test29_leavingDoesNotDecreaseCount(self):
        """Players who leave do not actually cease being observers, and
        therefore do not decrease max join count.  Note this works
        differently with transient tables because the seating is
        automatic."""
        log_history.reset()
        self.table.factory.joined_max = 3
        self.assertEquals(self.table.factory.joined_count, 0)
        players = {}
        for ii in [ 1, 2, 3 ]:
            players[ii] = self.createPlayer(ii, getReadyToPlay = False)
            self.assertEqual(True, self.table.isJoined(players[ii]))
            self.assertEquals(self.table.factory.joined_count, ii)

        self.assertEquals(self.table.factory.joined_count, 3)
        
        self.assertEquals(log_history.get_all(), [])
        self.assertEquals(True, self.table.leavePlayer(players[1]))

        log_history.reset()
        players[4] = self.createPlayer(4, getReadyToPlay = False)
        self.checkFailedJoinDueToMax(players[4])

        self.assertEquals(self.table.factory.joined_count, 3)
    # -------------------------------------------------------------------
    def test30_justEnoughPlayers(self):
        """Tests situation where players truely are gone from the table
        and are no longer observers either, thus allowing more players to
        be conntected.  With transient tables, this automatically tries to
        seat them."""
        log_history.reset()
        self.table.factory.joined_max = 3
        players = {}
        for ii in [ 1, 2, 3 ]:
            players[ii] = self.createPlayer(ii, getReadyToPlay = True)
            self.assertEqual(True, self.table.isJoined(players[ii]))
            self.assertEquals(self.table.factory.joined_count, ii)
        messages = log_history.get_all()
        messages_string = "\n".join(messages)
        self.failUnlessSubstring('player 1 gets seat 1', messages_string)
        self.failUnlessSubstring('player 2 gets seat 6', messages_string)
        self.failUnlessSubstring('player 3 gets seat 3', messages_string)
        log_history.reset()
        self.assertEquals(self.table.factory.joined_count, 3)

        for ii in [ 4, 5, 6 ]:
            players[ii] = self.createPlayer(ii, getReadyToPlay = False)
            self.checkFailedJoinDueToMax(players[ii])
            log_history.reset()
            self.assertEquals(self.table.factory.joined_count, 3)

        # leavePlayer turns an actual player into an observer, so they are still
        #  connected.  player 4 should still be unable to join.
        self.assertEquals(True, self.table.leavePlayer(players[1]))
        self.doJoinAndFailDueToMax(players[4])
        log_history.reset()
        self.assertEquals(self.table.factory.joined_count, 3)

        self.assertEquals(True, self.table.quitPlayer(players[2]))
        log_history.search('[Server][PokerGame %d] removing player %d from game'
                      % (self.table.game.id, players[2].serial))
        log_history.reset()
        self.assertEquals(self.table.factory.joined_count, 2)
        self.table.joinPlayer(players[4])
        self.assertEqual(True, self.table.isJoined(players[4]))
        self.assertEquals(log_history.get_all(), [])
        self.assertEquals(self.table.factory.joined_count, 3)

        self.assertEquals(None, self.table.kickPlayer(players[3].serial))
        log_history.search('[Server][PokerGame %d] removing player %d from game'
                      % (self.table.game.id, players[3].serial))
        self.assertEquals(self.table.factory.joined_count, 3)

        self.doJoinAndFailDueToMax(players[5])
        log_history.reset()
        self.assertEquals(self.table.factory.joined_count, 3)

        self.assertEquals(True, self.table.disconnectPlayer(players[3]))
        log_history.search('[Server][PokerGame %d] removing player %d from game'
                      % (self.table.game.id, players[3].serial))
        log_history.reset()
        self.assertEquals(self.table.factory.joined_count, 2)

        self.table.joinPlayer(players[5])
        self.assertEqual(True, self.table.isJoined(players[5]))
        self.assertEquals(log_history.get_all(), [])
        self.assertEquals(self.table.factory.joined_count, 3)

        self.doJoinAndFailDueToMax(players[6])
        log_history.reset()
        self.assertEquals(self.table.factory.joined_count, 3)
    # -------------------------------------------------------------------
    def test31_kickPlayerForMissingTooManyBlinds(self):
        """SKIP THIS TEST IN THIS SUBCLASS
        """
        return True
# --------------------------------------------------------------------------------
class MockServiceWithLadder(MockService):
    def __init__(self, settings):
        MockService.__init__(self, settings)
        self.has_ladder = True
        self.calledLadderMockup = None

    def getLadder(self, game_id, currency_serial, user_serial):
        self.calledLadderMockup = user_serial
        return PacketPokerPlayerStats()
    
# --------------------------------------------------------------------------------
class PokerTableMoveTestCase(PokerTableTestCaseBase):
    def setUp(self, ServiceClass = MockServiceWithLadder):
        PokerTableTestCaseBase.setUp(self, ServiceClass = MockServiceWithLadder)

    # -------------------------------------------------------------------
    def test15_moveTo(self):
        """Make sure a player can move from one place to another"""
        player = self.createPlayer(1)
        player.reasonExpected = "MockMoveTest"

        otherTablePlayer = self.createPlayer(2, table=self.table2)

        expectPlayerDeferred = otherTablePlayer.waitFor(PACKET_POKER_PLAYER_ARRIVE)
        def checkReturnPacket(packet):
            self.assertEqual(packet.name, "Player1")
            self.assertEqual(packet.game_id, self.table2_value)
            self.assertEquals(self.service.calledLadderMockup, packet.serial)
            self.assertEquals('dealTimeout' in self.table2.timer_info, False)
        expectPlayerDeferred.addCallback(checkReturnPacket)

        #
        # Artificial timer that must be removed when the autodeal functions are
        # called as a side effect of moving the player to the table2
        #
        self.table2.timer_info['dealTimeout'] = reactor.callLater(200000, lambda: True)
        self.table_joined = None
        def checkJoin(table, reason):
            self.table_joined = table
        player.join = checkJoin
        self.table.movePlayer(1, self.table2.game.id, reason="MockMoveTest")
        self.assertEquals(self.table_joined, self.table2)
        return expectPlayerDeferred


# --------------------------------------------------------------------------------
class PokerTableRejoinTestCase(PokerTableTestCaseBase):
    def setUp(self, ServiceClass = MockServiceWithLadder):
        PokerTableTestCaseBase.setUp(self, ServiceClass = MockServiceWithLadder)

    def test49_playerRejoinCheckAutoFlag(self):
        player1 = self.createPlayer(1, clientClass=MockClientWithRealJoin)
        player1.service = self.service
        player2 = self.createPlayer(2)
        
        def quitPlayer(x):
            self.table.quitPlayer(player1)
            self.assertEquals(True, self.table.game.serial2player[1].isAuto())
        def joinPlayer(x):
            d = player1.waitFor(PACKET_POKER_PLAYER_ARRIVE)
            self.table.joinPlayer(player1)
            return d
        def checkAutoFlag(x):
            playerArrive1 = [p for p in player1.packets if p.type == PACKET_POKER_PLAYER_ARRIVE and p.serial == 1]
            self.assertEquals(False, self.table.game.serial2player[1].isAuto())            
            self.assertEquals(False, playerArrive1[0].auto)
            
        d = player1.waitFor(PACKET_POKER_START)
        d.addCallback(quitPlayer)
        d.addCallback(joinPlayer)
        d.addCallback(checkAutoFlag)
        
        self.table.scheduleAutoDeal()
        return d
    

# --------------------------------------------------------------------------------
class PokerTableExplainedTestCase(PokerTableTestCaseBase):
    """This suite tries to cover edge cases of errors happening during gameplay within
    the PokerExplain and their respective PokerGameClient classes."""

    def setUp(self, ServiceClass = MockServiceWithLadder):
        PokerTableTestCaseBase.setUp(self, ServiceClass = MockServiceWithLadder)
        
    def test50_fold_immediately(self):
        table = self.table
        game = table.game
        
        serials = [1,2]
        clients = {}
        
        for serial in serials:
            clients[serial] = client = self.createPlayer(serial, getReadyToPlay=False, clientClass=MockClientWithExplain)
            client.service = self.service
            client.setExplain(PacketPokerExplain.ALL)
            self.assertEqual(True, table.joinPlayer(client, reason="MockCreatePlayerJoin"))
            self.assertEqual(True, table.seatPlayer(client, -1))
            self.assertEqual(True, table.buyInPlayer(client, game.maxBuyIn()))
            table.sitPlayer(client)
            game.noAutoBlindAnte(serial)
            
        for serial,client in clients.iteritems():
            client.setMoney(table, 1)
        
        table.scheduleAutoDeal()
        
        def payBlinds(packet):
            game.blind(2); table.update()
            game.blind(1); table.update()
            
            # game is finished by now in the PokerGameServer, but
            # the changes are not propageted to the PokerGameClients until 
            # we update the table
            self.assertTrue(game.isEndOrNull())
            
        d = clients[1].waitFor(PACKET_POKER_BLIND_REQUEST)
        d.addCallback(payBlinds)
        
        return d
    
    def test51_blindAnteState(self):
        table = self.table3
        # receive a PACKET_POKER_STATE when in state blind_ante. players have to have the same bet
        serials = [1,2]
        clients = {}
        for serial in serials:
            clients[serial] = client = self.createPlayer(serial, getReadyToPlay=False, clientClass=MockClientWithExplain)
            client.service = self.service
            # client.setExplain(PacketPokerExplain.ALL)
            self.assertEqual(True, table.joinPlayer(client, reason="MockCreatePlayerJoin"))
            self.assertEqual(True, table.seatPlayer(client, -1))
            self.assertEqual(True, table.buyInPlayer(client, table.game.maxBuyIn()))
            table.sitPlayer(client)
            table.game.noAutoBlindAnte(serial)

        for serial,client in clients.iteritems():
            client.setMoney(table,30)
        
        def waitForPosition(packet):
            table.game.serial2player[1].sit_out_next_turn = True
            table.game.serial2player[2].sit_out_next_turn = True
            table.game.callNraise(2, 2000)
            table.update()
            table.game.call(1)
            table.update()
            table.destroy()
        
        def payBlinds(packet):
            table.game.blind(2,25)
            table.game.blind(1,20)
            d2 = clients[1].waitFor(PACKET_POKER_POSITION)
            d2.addCallback(waitForPosition)
            table.update()
            return d2
            
        d1 = clients[1].waitFor(PACKET_POKER_BLIND_REQUEST)
        
        d1.addCallback(payBlinds)
        table.scheduleAutoDeal()
        
        return d1
    
    def test52_strange(self):
        table = self.table3
        clients = {}
        table.oldUpdate = table.update
        def newUpdate(obj):
            #print '[TABLE_POS]',obj.game.position,obj.game.player_list[obj.game.position] if len(obj.game.player_list)>obj.game.position and obj.game.position >= 0 else 'NOT AVAILABLE: pos: %s, serials: %s' % (obj.game.position, obj.game.player_list)
            return obj.oldUpdate()
        table.update = lambda: newUpdate(table)
        def sitIn(serial):
            client = clients[serial]
            table.seatPlayer(client, -1)
            table.buyInPlayer(client, table.game.maxBuyIn())
            table.game.noAutoBlindAnte(serial)
            table.sitPlayer(client)
            table.update()
            
        for serial in (1,2,3):
            clients[serial] = client = self.createPlayer(serial, getReadyToPlay=False, clientClass=MockClientWithExplain)
            client.service = self.service
            client.setExplain(PacketPokerExplain.REST)
            table.joinPlayer(client)
            
        for serial in (1,2):
            sitIn(serial)
            
        def quitAll(packet):
            table.destroy()
            
        def secondGame(packet):
            table.game.blind(1); table.update()
            table.game.blind(3); table.update()
            table.game.fold(2); table.update()
            d3 = clients[1].waitFor(PACKET_POKER_TIMEOUT_NOTICE)
            d3.addCallback(lambda packet: table.destroy())
            return d3
            
        def firstGame(packet):
            table.game.blind(2); table.update()
            table.game.blind(1); table.update()
            sitIn(3)
            table.game.fold(2); table.update()
            d2 = clients[1].waitFor(PACKET_POKER_BLIND_REQUEST)
            d2.addCallback(secondGame)
            table.scheduleAutoDeal()
            return d2
        
        d1 = clients[1].waitFor(PACKET_POKER_BLIND_REQUEST)
        
        d1.addCallback(firstGame)
        table.scheduleAutoDeal()
        return d1
    
    def test53_uncalled(self):
        table = self.table9
        
        player_list = [101, 102, 103, 104, 105, 106, 107]
        clients = {}
        
        decks = []
        cards = []
        cards_to_player = [
            (101,(1,1)),
            (102,(1,1)),
            (103,(207, 206)), 
            (104,(203, 235)),
            (105,(236, 239)), 
            (106,(1,1)),
            (107,(1,1))
        ]
        cards_board = (28, 51, 41, 24, 45)
        for i in range(2):
            for (_player,card_strings) in cards_to_player:
                cards.append(card_strings[i])
        cards.extend(cards_board)
        cards.reverse()
        decks.append(cards)
        decks.append(cards)
        
        table.game.shuffler = PokerPredefinedDecks(decks)
            
        def sitIn(serial):
            client = clients[serial]
            table.seatPlayer(client, -1)
            table.buyInPlayer(client, table.game.maxBuyIn())
            table.game.noAutoBlindAnte(serial)
            table.sitPlayer(client)
            table.update()
            
        for serial in player_list:
            clients[serial] = client = self.createPlayer(serial, getReadyToPlay=False, clientClass=MockClientWithExplain)
            client.service = self.service
            client.setExplain(PacketPokerExplain.REST)
            table.joinPlayer(client)
        for serial in player_list:
            sitIn(serial)

        table.game.getPlayer(103).money = 1000000
        clients[103].setMoney(table,1000000)
        table.update()
        
        def secondGame(packet):
            table.game.fold(103); table.update() # is ignored
            table.leavePlayer(clients[103])
            table.game.blind(104); table.update()
            table.game.blind(103); table.update() # is ignored
            table.game.blind(105); table.update()
            
            for client in clients.values():
                explain_game = clients[106].explain.games.getGame(table.game.id)
                self.assertEqual(table.game.uncalled_serial, explain_game.uncalled_serial)
            
            table.game.check(106); table.update() # ignored
            table.playerTimeoutTimer(106)
            
            table.game.check(107); table.update() # ignored
            table.game.check(107); table.update() # ignored
            table.playerTimeoutTimer(107)
            
            table.playerTimeoutTimer(101)
            table.playerTimeoutTimer(102)
            
            table.game.check(104); table.update() # ignored
            table.playerTimeoutTimer(104)
            
            table.destroy()
            
        def firstGame(packet):
            table.game.blind(102); table.update()
            table.game.blind(103); table.update()
            table.game.call(104); table.update()
            table.game.call(105); table.update()
            table.game.fold(106); table.update()
            table.game.fold(107); table.update()
            table.game.fold(101); table.update()
            table.game.call(102); table.update()
            
            table.game.check(103); table.update()
            table.game.check(102); table.update()
            table.game.check(104); table.update()
            table.game.check(105); table.update()
            
            table.game.check(102); table.update()
            table.game.callNraise(103, 4); table.update()
            table.game.call(104); table.update()
            table.game.call(105); table.update()
            table.game.fold(102); table.update()
            
            table.game.callNraise(103, 280); table.update()
            table.game.call(104); table.update()
            table.game.callNraise(105, 560); table.update()
            
            table.game.callNraise(103, 100000000000); table.update()
            table.game.fold(104); table.update()
            table.game.call(105); table.update()
            d2 = clients[103].waitFor(PACKET_POKER_BLIND_REQUEST)
            d2.addCallback(secondGame)
            table.scheduleAutoDeal()
            return d2
            
        d1 = clients[102].waitFor(PACKET_POKER_BLIND_REQUEST)
        d1.addCallback(firstGame)
        table.game.forced_dealer_seat = 0
        table.scheduleAutoDeal()
        return d1

    def test54_autorebuy_off(self):
        return self._test54_autorebuy("normal")
    
    def test54_autorebuy(self):
        return self._test54_autorebuy("rebuy")
    
    def test54_autorefill_off(self):
        return self._test54_autorebuy("refill")
    
    def test54_autorefill_2(self):
        return self._test54_autorebuy("refill2")

    def _test54_autorebuy(self, mode="normal"):
        table = self.table9
        game = table.game
        player_list = [101, 102, 103]
        clients = {}

        decks = []
        cards = []
        cards_to_player = [
            (101,(207, 206)),
            (102,(203, 235)),
            (103,(236, 239)),
        ]
        cards_board = (28, 51, 41, 24, 45)
        for i in range(2):
            for (_player,card_strings) in cards_to_player:
                cards.append(card_strings[i])
        cards.extend(cards_board)
        cards.reverse()
        decks.append(cards)
        decks.append(cards)

        game.shuffler = PokerPredefinedDecks(decks)

        def sitIn(serial):
            client = clients[serial]
            table.seatPlayer(client, -1)
            table.buyInPlayer(client, game.maxBuyIn())
            game.noAutoBlindAnte(serial)
            table.sitPlayer(client)
            table.update()

        for serial in player_list:
            clients[serial] = client = self.createPlayer(serial, getReadyToPlay=False)
            client.service = self.service
            table.joinPlayer(client)
        for serial in player_list:
            sitIn(serial)

        if mode == "rebuy":
            table.autoRebuy(102, 1)
        elif mode.startswith("refill"):
            table.autoRefill(102, 3)

        def secondGame(packet):
            if mode == "normal":
                return # "There is no second hand"
            elif mode == "rebuy":
                assert 103 in game.serialsInGame()
                assert 102 in game.serialsInGame()
                assert game.serial2player[102].money == game.buyIn()
            elif mode == "refill":
                assert 103 in game.serialsInGame()
                assert 102 in game.serialsInGame()
                assert game.serial2player[102].money == game.maxBuyIn()
            elif mode == "refill2":
                assert 103 in game.serialsInGame()
                assert 102 in game.serialsInGame()
                assert game.serial2player[102].money == game.maxBuyIn(), "[%s] game.serial2player[102].money == game.maxBuyIn() (%s != %s)" % (game.state, game.serial2player[102].money, game.maxBuyIn())
            table.destroy()

        def firstGame(packet):
            log_history.reset()

            game.blind(102); table.update()
            game.blind(103); table.update()
            game.call(101); table.update()

            game.call(102); table.update()
            game.check(103); table.update()

            raise_amount = game.serial2player[102].money
            if mode == "refill2": raise_amount /= 2
            
            game.callNraise(102, raise_amount); table.update()
            game.call(103); table.update()
            game.call(101); table.update()
            while game.isRunning():
                game.check(game.getSerialInPosition())

            if mode == "normal":
                return
            d = clients[103].waitFor(PACKET_POKER_START)
            table.scheduleAutoDeal()
            return d

        d = clients[102].waitFor(PACKET_POKER_BLIND_REQUEST)
        d.addCallback(firstGame)
        d.addCallback(secondGame)
        
        
        table.game.forced_dealer_seat = 0
        table.scheduleAutoDeal()
        return d

    def test54_serial2delta(self):
        class MockLockCheck(object):
            _timeout = 5*60*60
            def start(self):
                pass
            def stop(self):
                pass
               
        table = self.table9
        table._lock_check = MockLockCheck()
        player_list = [85554, 85562, 55742]
        
        def sitIn(serial):
            client = clients[serial]
            table.seatPlayer(client, -1)
            table.buyInPlayer(client, table.game.maxBuyIn())
            table.game.noAutoBlindAnte(serial)
            table.sitPlayer(client)
            table.update()

        clients = {}
            
        for serial in player_list:
            clients[serial] = client = self.createPlayer(serial, getReadyToPlay=False, clientClass=MockClientWithExplain)
            client.service = self.service
            client.setExplain(PacketPokerExplain.REST)
            table.joinPlayer(client)
        for serial in player_list:
            sitIn(serial)
        table.sitOutPlayer(table.avatar_collection.get(85562)[0])

        def game2(packet):
            table.game.blind(85554); table.update()
            table.leavePlayer(clients[85562])
            table.game.blind(55742); table.update()
            table.game.check(85554); table.update() # ignored
            table.leavePlayer(clients[85554])
            table.destroy()

        def game1(packet):
            table.game.blind(55742); table.update()
            table.game.blind(85554); table.update()
            table.game.call(55742); table.update()
            table.game.check(85554); table.update()
            table.game.check(55742); table.update()
            table.game.check(85554); table.update()
            table.game.callNraise(55742, 4); table.update()
            table.game.fold(85554); table.update()
            d2 = clients[85554].waitFor(PACKET_POKER_BLIND_REQUEST)
            d2.addCallback(game2)
            table.sitPlayer(clients[85562]) ; table.update()
            table.scheduleAutoDeal()
            return d2
            

        d = clients[85554].waitFor(PACKET_POKER_BLIND_REQUEST)
        d.addCallback(game1)
        table.game.forced_dealer_seat = 0
        table.scheduleAutoDeal()
        return d

    def test55_assertionTest(self):
        self.table = table = self.table9
        game = table.game
        self.service.has_ladder = False
        table.factory.buyInPlayer = lambda *a,**kw: table.game.maxBuyIn()
        
        def addPlayerAndReorder(self, *a,**kw):
            was_added = self.addPlayerOrig(*a,**kw)
            if was_added:
                self.serial2player = OrderedDict(sorted(self.serial2player.items(),key=lambda (k,v):od_order.index(k)))
            return was_added
        
        game.addPlayerOrig = game.addPlayer
        game.addPlayer = lambda *a,**kw: addPlayerAndReorder(game,*a,**kw) 
        
        clients_all = {}
        clients = {}        
        s_all = [31464, 49610, 50317, 38108, 73933, 17030, 40712, 74280]
        s_sit = [50317, 38108, 73933, 17030, 40712, 74280]
        s_seats = [0,1,2,4,5,6,7,8,9]
        od_order = [49610, 17030, 74280, 73933, 40712, 50317, 31464, 52392, 38108]
        
        def checkPlayerlistAndMoney():
            pl = game.player_list
            mm = game.moneyMap()
            for client in clients_all.values():
                client_game = client.explain.games.getGame(game.id)
                self.assertTrue(pl==client_game.player_list)
                self.assertTrue(mm==client_game.moneyMap())
                
        def joinAndSeat(serial,should_sit,pos,explain=False):
            clients_all[serial] = client = self.createPlayer(serial, getReadyToPlay=False, clientClass=MockClientWithExplain)
            client.service = self.service
            if explain:
                client.setExplain(PacketPokerExplain.ALL)
            self.assertTrue(table.joinPlayer(client, reason="MockCreatePlayerJoin"))
            self.assertTrue(table.seatPlayer(client, pos))
            self.assertTrue(table.buyInPlayer(client, table.game.maxBuyIn()))
            table.game.noAutoBlindAnte(serial)
            if should_sit:
                clients[serial] = client
                table.sitPlayer(client)
            table.update()
        
        #
        # get seats for all players
        for pos,serial in enumerate(s_all):
            joinAndSeat(serial,serial in s_sit,s_seats[pos])
        #
        # setup game (forced_dealer does not work because of first_turn == False)
        table.game.dealer_seat = 1
        table.game.first_turn = False
        #
        # put missed_blind on None, if not all missed_blinds are reset
        for serial in s_all:
            table.game.serial2player[serial].missed_blind = None
        #
        # setup 49610 
        table.game.serial2player[49610].missed_blind = 'small'
        #
        # setup 52392 (only observer)
        s_all.append(52392)
        clients_all[52392] = client = self.createPlayer(52392, getReadyToPlay=False, clientClass=MockClientWithExplain)
        client.service = self.service
        client.setExplain(PacketPokerExplain.ALL)
        self.assertTrue(table.joinPlayer(client, reason="MockCreatePlayerJoin"))
        
        def firstGame(packet):
            #
            # 49610 joins again
            clients[49610] = clients_all[49610]
            s_sit.append(49610)
            self.assertFalse(table.seatPlayer(clients[49610], -1))
            table.game.noAutoBlindAnte(49610)
            table.sitPlayer(clients[49610])
            table.update()
            #
            # get a seat for 52392
            self.assertTrue(table.seatPlayer(clients_all[52392], 3))
            self.assertTrue(table.buyInPlayer(clients_all[52392], table.game.maxBuyIn()))
            table.game.noAutoBlindAnte(52392)
            table.update()
            table.game.blind(38108); table.update()
            table.game.blind(73933); table.update()
            table.quitPlayer(clients_all[31464]); table.update()
            joinAndSeat(31464, True, -1,True)
            
        table.scheduleAutoDeal()
        d1 = clients[s_sit[0]].waitFor(PACKET_POKER_START)
        d1.addCallback(firstGame)
        return d1
    
    def test56_strange(self):
        self.table = table = self.table9
        game = table.game
        self.service.has_ladder = False
        table.factory.buyInPlayer = lambda *a,**kw: table.game.maxBuyIn()
        
        def addPlayerAndReorder(self, *a,**kw):
            was_added = self.addPlayerOrig(*a,**kw)
            if was_added:
                self.serial2player = OrderedDict(sorted(self.serial2player.items(),key=lambda (k,v):od_order.index(k)))
            return was_added
        
        game.addPlayerOrig = game.addPlayer
        game.addPlayer = lambda *a,**kw: addPlayerAndReorder(game,*a,**kw) 
        
        clients_all = {}
        clients = {}        
        s_sit = [61615, 106548, 1789, 43743]
        s_all = [61615, 1789, 43743, 106548, 40712]
        s_seats = [0,1,2,4,5,6,7,8,9]
        od_order = [61615, 106548, 1789, 43743, 40712]
        
        def checkPlayerlistAndMoney():
            pl = game.player_list
            mm = game.moneyMap()
            for client in clients_all.values():
                client_game = client.explain.games.getGame(game.id)
                self.assertTrue(pl==client_game.player_list)
                self.assertTrue(mm==client_game.moneyMap())
                
        def joinAndSeat(serial,should_sit,pos,explain=True):
            clients_all[serial] = client = self.createPlayer(serial, getReadyToPlay=False, clientClass=MockClientWithExplain)
            client.service = self.service
            if explain:
                client.setExplain(PacketPokerExplain.REST)
            self.assertTrue(table.joinPlayer(client, reason="MockCreatePlayerJoin"))
            self.assertTrue(table.seatPlayer(client, pos))
            self.assertTrue(table.buyInPlayer(client, table.game.maxBuyIn()))
            table.game.noAutoBlindAnte(serial)
            if should_sit:
                clients[serial] = client
                table.sitPlayer(client)
            table.update()
        
        #
        # get seats for all players
        for pos,serial in enumerate(s_all):
            joinAndSeat(serial,serial in s_sit,s_seats[pos])
        #
        # setup game (forced_dealer does not work because of first_turn == False)
        table.game.dealer_seat = 1
        table.game.first_turn = False
        #
        # put missed_blind on None, if not all missed_blinds are reset
        for serial in s_all:
            table.game.serial2player[serial].missed_blind = None
        #
        # setup 49610 
        table.game.serial2player[40712].missed_blind = 'small'
        #
        def firstGame(packet):
            clients[40712] = clients_all[40712]
            s_sit.append(40712)
            self.assertFalse(table.seatPlayer(clients[40712], -1))
            table.game.noAutoBlindAnte(40712)
            table.sitPlayer(clients[40712])
            table.update()
            table.game.blind(106548); table.update()
            table.game.blind(61615)
            table.update()
                
            
        log_history.reset()
        table.scheduleAutoDeal()
        d1 = clients[s_sit[0]].waitFor(PACKET_POKER_START)
        d1.addCallback(firstGame)
        return d1

    def test59_manyDisconnects(self):
        self.table = table = self.table9
        game = table.game
        self.service.has_ladder = False
        table.factory.buyInPlayer = lambda *a,**kw: table.game.maxBuyIn()
        
        def addPlayerAndReorder(self, *a,**kw):
            was_added = self.addPlayerOrig(*a,**kw)
            if was_added:
                self.serial2player = OrderedDict(sorted(self.serial2player.items(),key=lambda (k,v):od_order.index(k)))
            return was_added
        
        game.addPlayerOrig = game.addPlayer
        game.addPlayer = lambda *a,**kw: addPlayerAndReorder(game,*a,**kw) 
        

        clients_all = {}
        clients = {}        
        s_sit = [155139, 154961, 26908, 155165]
        s_all = [95972, 154961, 155165, 155139, 26908]
        s_seats = [0,1,2,3,6]
        od_order = [155139, 154961, 26908, 155165, 95972, 152688]
                
        def joinAndSeat(serial,should_sit,pos,explain=False):
            clients_all[serial] = client = self.createPlayer(serial, getReadyToPlay=False, clientClass=MockClientWithExplain)
            client.service = self.service
            if explain:
                client.setExplain(PacketPokerExplain.REST)
            self.assertTrue(table.joinPlayer(client, reason="MockCreatePlayerJoin"))
            self.assertTrue(table.seatPlayer(client, pos))
            self.assertTrue(table.buyInPlayer(client, table.game.maxBuyIn()))
            table.game.noAutoBlindAnte(serial)
            if should_sit:
                clients[serial] = client
                table.sitPlayer(client)
            table.update()
        
        for pos,serial in enumerate(s_all):
            joinAndSeat(serial,serial in s_sit,s_seats[pos])
        
        #
        # setup game (forced_dealer does not work because of first_turn == False)
        table.game.dealer_seat = 3
        table.game.first_turn = False
        #
        # put missed_blind on None, if not all missed_blinds are reset
        for serial in s_all:
            table.game.serial2player[serial].missed_blind = None
        
        table.game.serial2player[95972].missed_blind = 'small'
        table.game.serial2player[95972].money = 0
        
        def firstGame(packet):
            # player 95972 rebuys and sits in
            table.rebuyPlayerRequest(95972, 0)
            table.sitPlayer(clients_all[95972])
            clients[95972] = clients_all[95972]
            table.update()
            
            # a new player arrives
            joinAndSeat(152688, False, 5)
            
            # two players time out
            table.timer_info["playerTimeout"].cancel()
            table.playerTimeoutTimer(154961)
            
            table.timer_info["playerTimeout"].cancel()
            table.playerTimeoutTimer(155165)
            
            # one player pays the small blind
            game.blind(155139)
            table.update()
            
            # the new player leaves again
            table.disconnectPlayer(clients_all[152688])
            
            # another player times out
            table.timer_info["playerTimeout"].cancel()
            table.playerTimeoutTimer(26908)
            
        log_history.reset()
        table.scheduleAutoDeal()
        d1 = clients[s_sit[0]].waitFor(PACKET_POKER_START)
        d1.addCallback(firstGame)
        return d1
    
    def test60_minArgEmpty(self):
        self.table = table = self.table9
        game = table.game
        self.service.has_ladder = False
        table.factory.buyInPlayer = lambda *a,**kw: table.game.maxBuyIn()
        
        def addPlayerAndReorder(self, *a,**kw):
            was_added = self.addPlayerOrig(*a,**kw)
            if was_added:
                self.serial2player = OrderedDict(sorted(self.serial2player.items(),key=lambda (k,v):od_order.index(k)))
            return was_added
        
        game.addPlayerOrig = game.addPlayer
        game.addPlayer = lambda *a,**kw: addPlayerAndReorder(game,*a,**kw) 
        
        clients_all = {}
        clients = {}
        s_sit = [154625, 43850, 75617, 56120]
        s_all = [43850, 155397, 154625, 75617, 56120, 29546, 155411]
    
        s_seats = [0,1,2,3,4,5,6,7,8]
        od_order = [154625, 43850, 75617, 56120, 155397, 29546, 155411]
        
        def joinAndSeat(serial,should_sit,pos,explain=True):
            clients_all[serial] = client = self.createPlayer(serial, getReadyToPlay=False, clientClass=MockClientWithExplain)
            client.service = self.service
            if explain:
                client.setExplain(PacketPokerExplain.REST)
            self.assertTrue(table.joinPlayer(client, reason="MockCreatePlayerJoin"))
            self.assertTrue(table.seatPlayer(client, pos))
            self.assertTrue(table.buyInPlayer(client, table.game.maxBuyIn()))
            table.game.noAutoBlindAnte(serial)
            if should_sit:
                clients[serial] = client
                table.sitPlayer(client)
            table.update()

        
        for pos,serial in enumerate(s_all):
            joinAndSeat(serial,serial in s_sit,s_seats[pos])

        #
        # setup game (forced_dealer does not work because of first_turn == False)
        table.game.dealer_seat = 3
        table.game.first_turn = False
        #
        # put missed_blind on None, if not all missed_blinds are reset
        for serial in s_all:
            table.game.serial2player[serial].missed_blind = None

        for richer in (43850,75617,56120):
            game.serial2player[richer].money *= 2
            
        def firstGame(packet):
            #
            # rebuy of 155411
            table.rebuyPlayerRequest(155411, 0)
            table.sitPlayer(clients_all[155411])
            clients[155411] = clients_all[155411]
            table.update()
            
            game.blind(43850); table.update()
            game.blind(154625); table.update()
            game.blind(155411); table.update()
            
            # 155397 disconnects
            table.disconnectPlayer(clients_all[155397])
            
            game.callNraise(154625, game.serial2player[154625].money)
            # 155397 joins again
            joinAndSeat(155397, False, 1)
            
            game.call(75617); table.update()
            game.call(56120); table.update()
            game.call(155411); table.update()
            game.call(43850); table.update()
            # round finished
            game.check(43850); table.update()
            game.check(75617); table.update()
            game.check(56120); table.update()
            # round finished
            game.check(43850); table.update()
            game.check(75617); table.update()
            game.check(56120); table.update()
            # round finished
            game.check(43850); table.update()
            game.check(75617); table.update()
            game.check(56120); table.update()
            # game finished. error is caused here.
        
        log_history.reset()
        table.scheduleAutoDeal()
        d1 = clients[s_sit[0]].waitFor(PACKET_POKER_START)
        d1.addCallback(firstGame)
        
        return d1
    
    def test61_incongruentMoney(self):
        self.table = table = self.table9
        game = table.game
        self.service.has_ladder = False
        table.factory.buyInPlayer = lambda *a,**kw: table.game.maxBuyIn()
        
        def addPlayerAndReorder(self, *a,**kw):
            was_added = self.addPlayerOrig(*a,**kw)
            if was_added:
                self.serial2player = OrderedDict(sorted(self.serial2player.items(),key=lambda (k,v):od_order.index(k)))
            return was_added
        
        game.addPlayerOrig = game.addPlayer
        game.addPlayer = lambda *a,**kw: addPlayerAndReorder(game,*a,**kw) 
        
        clients_all = {}
        clients = {}
                
        s_sit = [82247, 58255, 117776, 55572, 105398]
        s_all = [105398, 114305, 58255, 82247, 55572, 117776, 102475, 29047]
        
        s_seats = [0,1,2,3,4,5,6,7,8]
        od_order = [82247, 58255, 117776, 55572, 105398, 114305, 102475, 29047]
        
        def joinAndSeat(serial, should_sit, pos, explain=True):
            clients_all[serial] = client = self.createPlayer(serial, getReadyToPlay=False, clientClass=MockClientWithExplain)
            client.service = self.service
            if explain: client.setExplain(PacketPokerExplain.REST)
            self.assertTrue(table.joinPlayer(client, reason="MockCreatePlayerJoin"))
            self.assertTrue(table.seatPlayer(client, pos))
            self.assertTrue(table.buyInPlayer(client, table.game.maxBuyIn()))
            table.game.noAutoBlindAnte(serial)
            if should_sit:
                clients[serial] = client
                table.sitPlayer(client)
            table.update()

        def joinAgain(serial, pos, explain=True):
            clients_all[serial] = client = self.createPlayer(serial, getReadyToPlay=False, clientClass=MockClientWithExplain)
            client.service = self.service
            if explain: client.setExplain(PacketPokerExplain.REST)
            self.assertTrue(table.joinPlayer(client, reason="MockCreatePlayerJoin"))
            self.assertTrue(table.seatPlayer(client, pos))
            self.assertTrue(table.buyInPlayer(client, table.game.maxBuyIn()))
            
        for pos,serial in enumerate(s_all):
            joinAndSeat(serial,serial in s_sit,s_seats[pos])

        #
        # setup game (forced_dealer does not work because of first_turn == False)
        table.game.dealer_seat = 4
        table.game.first_turn = False
        #
        # put missed_blind on None, if not all missed_blinds are reset
        for serial in s_all:
            table.game.serial2player[serial].missed_blind = None
        
        game.serial2player[82247].money *= 2
        game.serial2player[29047].money *= 3
        game.serial2player[114305].money *= 4
        
        for player_serial, player in game.serial2player.iteritems():
            for c in clients_all.itervalues():
                c.explain.games.getGame(game.id).serial2player[player_serial].money = player.money
            
        table.game.serial2player[114305].missed_blind = 'big'
        table.game.serial2player[102475].missed_blind = 'small'
        table.game.serial2player[29047].missed_blind = 'small'
                
        def firstGame(packet):
            # seated
            # 
            # 
            # players 29047,114305 sit in again
            table.seatPlayer(clients_all[29047], -1)
            table.sitPlayer(clients_all[29047])
            table.seatPlayer(clients_all[114305], -1)
            table.sitPlayer(clients_all[114305])
            
            # blinds
            game.blind(105398); table.update()
            game.blind(58255); table.update()
            game.blind(29047); table.update()
            game.blind(114305); table.update()
            log_history.reset()
            
            # player 102475 leaves
            table.leavePlayer(clients_all[102475]); table.update()
            joinAndSeat(102475, True, -1); table.update()
            
            game.check(114305); table.update()
            game.check(58255); table.update()
            game.callNraise(82247, game.serial2player[82247].money); table.update()
            game.fold(55572); table.update()
            game.fold(117776); table.update()
            game.call(29047); table.update()
            game.fold(105398); table.update()
            game.call(114305); table.update()
            game.fold(58255); table.update()
            
            game.callNraise(29047, game.serial2player[29047].money); table.update()
            game.call(114305); table.update()
            game.fold(82247); table.update()
            d ={}
            for player in game.serial2player.values():
                d[player.serial] = player.money

            self.assertEqual(d, game.showdown_stack[0]['serial2money'])
            
            for serial, client in clients_all.iteritems():
                ex_money_map = client.explain.games.getGame(game.id).moneyMap()
                self.assertEquals(game.moneyMap(), ex_money_map, 'moneyMap not equal for serial %d' % serial)
            
                    
        log_history.reset()
        table.scheduleAutoDeal()
        d1 = clients[s_sit[0]].waitFor(PACKET_POKER_START)
        d1.addCallback(firstGame)
        
        return d1
    
    def test62_doubleTheMoney(self):

        self.table = table = self.table9
        game = table.game
        self.service.has_ladder = False
        table.factory.buyInPlayer = lambda *a,**kw: table.game.buyIn()
        
        def addPlayerAndReorder(self, *a,**kw):
            was_added = self.addPlayerOrig(*a,**kw)
            if was_added:
                self.serial2player = OrderedDict(sorted(self.serial2player.items(),key=lambda (k,v):od_order.index(k)))
            return was_added
        
        game.addPlayerOrig = game.addPlayer
        game.addPlayer = lambda *a,**kw: addPlayerAndReorder(game,*a,**kw) 
        
        clients_all = {}
        clients = {}
        
        s_sit = [158160, 11905, 155177, 65163, 60029, 30640, 79069]
        s_all = [158428, 11905, 155177, 65163, 158160, 79069, 60029, 30640]
        
        s_seats = [0,1,2,3,4,5,6,7,8]
        od_order = [158428, 11905, 155177, 65163, 158160, 79069, 60029, 30640]
        
        def joinAndSeat(serial, should_sit, pos, explain=True):
            clients_all[serial] = client = self.createPlayer(serial, getReadyToPlay=False, clientClass=MockClientWithExplain)
            client.service = self.service
            if explain: client.setExplain(PacketPokerExplain.REST)
            self.assertTrue(table.joinPlayer(client, reason="MockCreatePlayerJoin"))
            if table.seatPlayer(client, pos):
                self.assertTrue(table.buyInPlayer(client, game.maxBuyIn()))
                table.game.noAutoBlindAnte(serial)
                if should_sit:
                    clients[serial] = client
                    table.sitPlayer(client)
            table.update()
            
            
        for pos,serial in enumerate(s_all):
            joinAndSeat(serial,serial in s_sit,s_seats[pos])
            
        #
        # setup game (forced_dealer does not work because of first_turn == False)
        table.game.dealer_seat = 0
        table.game.first_turn = False
        #
        # put missed_blind on None, if not all missed_blinds are reset
        for serial in s_all:
            table.game.serial2player[serial].missed_blind = None
        
        # player is broke
        game.serial2player[158428].money = 0
        for c in clients_all.itervalues():
            c.explain.games.getGame(game.id).serial2player[158428].money = 0
            
        def firstGame(packet):
            # player rebuys
            table.rebuyPlayerRequest(158428, game.buyIn()); table.update()
            table.sitPlayer(clients_all[158428]); table.update()
            
            # players join later
            s_all.append(73780); od_order.append(73780)
            joinAndSeat(73780, True, -1); self.assertFalse(game.serial2player[73780].sit_out_next_turn)
            joinAndSeat(79069, False, -1)
            
            for serial, c in clients_all.iteritems():
                explain_money = c.explain.games.getGame(game.id).serial2player[158428].money
                game_money = game.serial2player[158428].money
                self.assertEqual(
                    explain_money,
                    game_money,
                    'explain(%d) thinks %d has %d chips but he has %d chips' % (serial, 158428, explain_money, game_money)
                )
        
        table.scheduleAutoDeal()
        d1 = clients[s_sit[0]].waitFor(PACKET_POKER_START)
        d1.addCallback(firstGame)
        
        return d1
    
    def test63_doubleTheMoney(self):
        self.table = table = self.table9
        game = table.game
        self.service.has_ladder = False
        table.factory.buyInPlayer = lambda serial, table_id, currency_serial, amount: amount 
        
#        deck info        
        cards_to_player = (
            ('9c','9h'),
            ('4c','5d'),
            ('Qh','Ah'),
            ('5c','4d'),
        )
        cards_board = ('2c','8h','3c','9d','4d')
#        build deck
        cards = []
        for i in range(2):
            for card_strings in cards_to_player:
                cards.append(table.game.eval.string2card(card_strings[i]))
        cards.extend(table.game.eval.string2card(cards_board))
        cards.reverse()
        
        table.game.deck = table.game.eval.card2string(cards)
        table.game.shuffler = PokerPredefinedDecks([cards])

        
        def addPlayerAndReorder(self, *a,**kw):
            was_added = self.addPlayerOrig(*a,**kw)
            if was_added:
                self.serial2player = OrderedDict(sorted(self.serial2player.items(),key=lambda (k,v):od_order.index(k)))
            return was_added
        
        game.addPlayerOrig = game.addPlayer
        game.addPlayer = lambda *a,**kw: addPlayerAndReorder(game,*a,**kw) 
        
        clients_all = {}
        clients = {}
        
        s_sit = [100, 200, 300, 400]
        s_all = [100, 200, 300, 400]
        
        s_seats = [0,1,2,3,4,5,6,7,8]
        od_order = [100, 200, 300, 400]
        
        def joinAndSeat(serial, should_sit, pos, explain=True):
            clients_all[serial] = client = self.createPlayer(serial, getReadyToPlay=False, clientClass=MockClientWithExplain)
            client.service = self.service
            if explain: client.setExplain(PacketPokerExplain.REST)
            self.assertTrue(table.joinPlayer(client, reason="MockCreatePlayerJoin"))
            if table.seatPlayer(client, pos):
                self.assertTrue(table.buyInPlayer(client, game.buyIn()))
                table.game.noAutoBlindAnte(serial)
                if should_sit:
                    clients[serial] = client
                    table.sitPlayer(client)
            table.update()
            
            
        for pos,serial in enumerate(s_all):
            joinAndSeat(serial,serial in s_sit,s_seats[pos])
            
        #
        # setup game (forced_dealer does not work because of first_turn == False)
        table.game.dealer_seat = 0
        table.game.first_turn = False
        #
        # put missed_blind on None, if not all missed_blinds are reset
        for serial in s_all:
            table.game.serial2player[serial].missed_blind = None
        
        def firstGame(packet):
            # player rebuys
            table.rebuyPlayerRequest(100, game.maxBuyIn()); table.update()
            table.seatPlayer(clients[100], -1); table.update()
            table.sitPlayer(clients[100]); table.update()
            game.blind(300); table.update()
            game.blind(400); table.update()
            game.callNraise(100, 500); table.update()
            for i in range(200,500,100):
                game.call(i); table.update()
            
            for i in range(3):
                game.check(300); table.update()
                game.check(400); table.update()
                game.check(100); table.update()
                game.check(200); table.update()
                
            for c_serial, c in clients.iteritems():
                for other_serial in game.serial2player.keys():
                    explain_money = c.explain.games.getGame(game.id).serial2player[other_serial].money
                    game_money = game.serial2player[other_serial].money
                    self.assertEquals(explain_money, game_money, 'explain(%d) thinks %d has %d chips but he has %d chips' % (c_serial, other_serial, explain_money, game_money))
                
        d1 = clients[s_sit[0]].waitFor(PACKET_POKER_START)
        d1.addCallback(firstGame)
        table.scheduleAutoDeal()
        
        return d1            
        
    def test64_lessMoney(self):
        self.table = table = self.table9
        game = table.game
        self.service.has_ladder = False
        table.factory.buyInPlayer = lambda serial, table_id, currency_serial, amount: amount 
        
#        deck info        
        cards_to_player = (
            ('6s','Ts'),
            ('Tc','Th'),
            ('7d','6h'),
            ('Td','5s'),
            ('Qs','8c'),
            ('6d','9h'),
            ('7s','3s'),
        )
        cards_board = ('Ad','9d','8d','5h','9s')

#        build deck
        cards = []
        for i in range(2):
            for card_strings in cards_to_player:
                cards.append(game.eval.string2card(card_strings[i]))
        cards.extend(game.eval.string2card(cards_board))
        cards.reverse()
        
        game.deck = game.eval.card2string(cards)
        game.shuffler = PokerPredefinedDecks([cards])

        
        def addPlayerAndReorder(self, *a,**kw):
            was_added = self.addPlayerOrig(*a,**kw)
            if was_added:
                self.serial2player = OrderedDict(sorted(self.serial2player.items(),key=lambda (k,v):od_order.index(k)))
            return was_added
        
        game.addPlayerOrig = game.addPlayer
        game.addPlayer = lambda *a,**kw: addPlayerAndReorder(game,*a,**kw) 
        
        clients_all = {}
        clients = {}
        
        s_sit = [158483, 158431, 158434, 160381, 160383]
        s_all = [160038, 69931, 158434, 160383, 158483, 158431, 160381, 160227]
        
        s_seats = [0,1,2,3,4,5,6,7,8]
        od_order = [160038, 69931, 158434, 160383, 158483, 158431, 160381, 160227, 154625]
        
        def joinAndSeat(serial, should_sit, pos, explain=True):
            clients_all[serial] = client = self.createPlayer(serial, getReadyToPlay=False, clientClass=MockClientWithExplain)
            client.service = self.service
            if explain: client.setExplain(PacketPokerExplain.REST)
            self.assertTrue(table.joinPlayer(client, reason="MockCreatePlayerJoin"))
            if table.seatPlayer(client, pos):
                self.assertTrue(table.buyInPlayer(client, game.buyIn()))
                table.game.noAutoBlindAnte(serial)
                if should_sit:
                    clients[serial] = client
                    table.sitPlayer(client)
            table.update()
            
        def checkExplainMoney():
            for c_serial, c in clients.iteritems():
                for other_serial in game.serial2player.keys():
                    explain_money = c.explain.games.getGame(game.id).serial2player[other_serial].money
                    game_money = game.serial2player[other_serial].money
                    self.assertEquals(explain_money, game_money, 'explain(%d) thinks %d has %d chips but he has %d chips' % (c_serial, other_serial, explain_money, game_money))
            
        for pos,serial in enumerate(s_all):
            joinAndSeat(serial,serial in s_sit,s_seats[pos])
            
        #
        # setup game (forced_dealer does not work because of first_turn == False)
        game.dealer_seat = 5
        game.first_turn = False
        #
        # put missed_blind on None, if not all missed_blinds are reset
        for serial in s_all:
            game.serial2player[serial].missed_blind = None
        
        game.serial2player[160227].missed_blind = 'n/a'
        game.serial2player[160038].missed_blind = 'small'
        game.serial2player[69931].missed_blind = 'small'
        
        def firstGame(packet):
#            log_history.reset()
            clients[160227] = clients_all[160227]
            joinAndSeat(160227, True, -1)
            
            clients[160038] = clients_all[160038]
            table.sitPlayer(clients_all[160038])
            table.update()
            
            game.blind(158434); table.update()
            
            clients[69931] = clients_all[69931]
            table.rebuyPlayerRequestNow(69931, game.buyIn())
            table.sitPlayer(clients[69931])
            table.update()
            
            game.blind(160383); table.update()
            game.blind(160038); table.update()
            game.blind(69931); table.update()
            
            game.call(158434); table.update()
            game.check(160383); table.update()
            
            game.call(158483); table.update()
            game.call(158431); table.update()
            game.call(160381); table.update()
            
            game.check(160038); table.update()
            
            game.callNraise(69931, game.serial2player[69931].money); table.update()
            game.fold(158434); table.update()
            
            game.fold(160383); table.update()
            game.fold(158483); table.update()
            game.fold(158431); table.update()
            
            game.call(160381); table.update()
            game.call(160038); table.update()
            
            joinAndSeat(154625, True, -1)
            
            checkExplainMoney()
            
        d1 = clients[s_sit[0]].waitFor(PACKET_POKER_START)
        d1.addCallback(firstGame)
        table.scheduleAutoDeal()
        return d1
    
    def test65_lateFirstRound(self):
        self.table = table = self.table9
        game = table.game
        self.service.has_ladder = False
        table.factory.buyInPlayer = lambda serial, table_id, currency_serial, amount: amount 

        def addPlayerAndReorder(self, *a,**kw):
            was_added = self.addPlayerOrig(*a,**kw)
            if was_added:
                self.serial2player = OrderedDict(sorted(self.serial2player.items(),key=lambda (k,v):od_order.index(k)))
            return was_added
        
        game.addPlayerOrig = game.addPlayer
        game.addPlayer = lambda *a,**kw: addPlayerAndReorder(game,*a,**kw) 
        
        clients_all = {}
        clients = {}

        s_sit = [10, 20, 30, 40, 50, 60]
        s_all = [10, 20, 30, 40, 50, 60]
        
        s_seats = [0,1,2,3,4,5,6,7,8]
        od_order = [10, 20, 30, 40, 50, 60, 70]

        def joinAndSeat(serial, should_sit, pos, explain=False):
            clients_all[serial] = client = self.createPlayer(serial, getReadyToPlay=False, clientClass=MockClientWithExplain)
            client.service = self.service
            if explain: client.setExplain(PacketPokerExplain.REST)
            self.assertTrue(table.joinPlayer(client, reason="MockCreatePlayerJoin"))
            if table.seatPlayer(client, pos):
                self.assertTrue(table.buyInPlayer(client, game.buyIn()))
                game.noAutoBlindAnte(serial)
                if should_sit:
                    clients[serial] = client
                    table.sitPlayer(client)
            table.update()
        
        for pos,serial in enumerate(s_all):
            joinAndSeat(serial,serial in s_sit,s_seats[pos])
        
        #
        # setup game (forced_dealer does not work because of first_turn == False)
        game.dealer_seat = 0
        game.first_turn = False
        #
        # put missed_blind on None, if not all missed_blinds are reset
        for serial in s_all:
            game.serial2player[serial].missed_blind = None
        
        def firstGame(packet):
            s_all.append(70); s_sit.append(70); joinAndSeat(70, True, 6)
            game.blind(30); table.update()
            game.blind(40); table.update()
            
            self.assertFalse(log_history.search("updateBlinds statement unexpectedly reached"))
            self.assertTrue(game.isFirstRound())
        
        d1 = clients[s_sit[0]].waitFor(PACKET_POKER_START)
        d1.addCallback(firstGame)
        table.scheduleAutoDeal()
        log_history.reset()
        return d1
    
    def test_allInDuringBlind(self):
        table = pokertable.PokerTable(self.service, 1111, {
            'name': "table11",
            'variant': "holdem",
            'betting_structure': "10-20_200-2000_no-limit",
            'seats': 9,
            'player_timeout' : 6, 
            'muck_timeout' : 1,
            'currency_serial': 0
        })
        
        game = table.game
        table.factory.buyInPlayer = lambda serial, table_id, currency_serial, amount: amount
        
        serials = [1,2]
        clients = {}

        def joinAndSeat(serial, should_sit, pos, explain=False):
            clients[serial] = client = self.createPlayer(serial, getReadyToPlay=False, clientClass=MockClientWithExplain)
            client.service = self.service
            if explain: client.setExplain(PacketPokerExplain.REST)
            self.assertTrue(table.joinPlayer(client, reason="MockCreatePlayerJoin"))
            if table.seatPlayer(client, pos):
                self.assertTrue(table.buyInPlayer(client, game.buyIn()))
                game.noAutoBlindAnte(serial)
                if should_sit:
                    clients[serial] = client
                    table.sitPlayer(client)
            table.update()
        
        joinAndSeat(1, True, -1, True)
        joinAndSeat(2, True, -1, False)
            
        game.dealer_seat = 0
        game.first_turn = False
        
        for serial in serials:
            game.serial2player[serial].missed_blind = None
        
        # player 2 has less money than the big blind. he will go all-in
        game.serial2player[2].money = game.bigBlind() - 1
        clients[1].explain.games.getGame(game.id).serial2player[2].money = game.bigBlind() - 1
        
        def firstGame(packet):
            game.blind(1); table.update()
            
            game.blind(2); table.update()
            self.assertTrue(game.serial2player[2].isAllIn())
            
            game.call(1); table.update()
            self.assertTrue(game.isEndOrNull())
            
        d1 = clients[serials[0]].waitFor(PACKET_POKER_START)
        d1.addCallback(firstGame)
        d1.addCallback(lambda res: table.destroy())
        table.scheduleAutoDeal()
        return d1
        
# --------------------------------------------------------------------------------

def GetTestSuite():
    seed(time.time())
    loader = runner.TestLoader()
    # loader.methodPrefix = "_test"
    suite = loader.suiteFactory()
    suite.addTest(loader.loadClass(PokerAvatarCollectionTestCase))
    suite.addTest(loader.loadClass(PokerTableTestCase))
    suite.addTest(loader.loadClass(PokerTableTestCaseWithPredefinedDecksAndNoAutoDeal))
    suite.addTest(loader.loadClass(PokerTableTestCaseTransient))
    suite.addTest(loader.loadClass(PokerTableMoveTestCase))
    suite.addTest(loader.loadClass(PokerTableRejoinTestCase))
    suite.addTest(loader.loadClass(PokerTableExplainedTestCase))
    return suite

def Run():
    return runner.TrialRunner(
        reporter.TextReporter,
        tracebackFormat='default',
    ).run(GetTestSuite())
# ------------------------------------------------------------------------------
if __name__ == '__main__':
    if Run().wasSuccessful():
        sys.exit(0)
    else:
        sys.exit(1)
