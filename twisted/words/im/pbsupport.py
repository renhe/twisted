# Twisted, the Framework of Your Internet
# Copyright (C) 2001-2002 Matthew W. Lefkowitz
#
# This library is free software; you can redistribute it and/or
# modify it under the terms of version 2.1 of the GNU Lesser General Public
# License as published by the Free Software Foundation.
#
# This library is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public
# License along with this library; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  USA

"""L{twisted.words} support for Instance Messenger."""

from __future__ import nested_scopes

from twisted.internet import defer
from twisted.internet import error
from twisted.python import log, components
from twisted.python.failure import Failure
from twisted.spread import pb

from twisted.words.im.locals import ONLINE, OFFLINE, AWAY

from twisted.words.im import basesupport, interfaces
from zope.interface import implements


class TwistedWordsPerson(basesupport.AbstractPerson):
    """I a facade for a person you can talk to through a twisted.words service.
    """
    def __init__(self, name, wordsAccount):
        basesupport.AbstractPerson.__init__(self, name, wordsAccount)
        self.status = OFFLINE

    def isOnline(self):
        return ((self.status == ONLINE) or
                (self.status == AWAY))

    def getStatus(self):
        return self.status

    def sendMessage(self, text, metadata):
        """Return a deferred...
        """
        if metadata:
            d=self.account.client.perspective.directMessage(self.name,
                                                            text, metadata)
            d.addErrback(self.metadataFailed, "* "+text)
            return d
        else:
            return self.account.client.perspective.callRemote('directMessage',self.name, text)

    def metadataFailed(self, result, text):
        print "result:",result,"text:",text
        return self.account.client.perspective.directMessage(self.name, text)

    def setStatus(self, status):
        self.status = status
        self.chat.getContactsList().setContactStatus(self)

class TwistedWordsGroup(basesupport.AbstractGroup):
    implements(interfaces.IGroup)
    def __init__(self, name, wordsClient):
        basesupport.AbstractGroup.__init__(self, name, wordsClient)
        self.joined = 0

    def sendGroupMessage(self, text, metadata=None):
        """Return a deferred.
        """
        #for backwards compatibility with older twisted.words servers.
        if metadata:
            d=self.account.client.perspective.callRemote(
                'groupMessage', self.name, text, metadata)
            d.addErrback(self.metadataFailed, "* "+text)
            return d
        else:
            return self.account.client.perspective.callRemote('groupMessage',
                                                              self.name, text)

    def setTopic(self, text):
        self.account.client.perspective.callRemote(
            'setGroupMetadata',
            {'topic': text, 'topic_author': self.client.name},
            self.name)

    def metadataFailed(self, result, text):
        print "result:",result,"text:",text
        return self.account.client.perspective.callRemote('groupMessage',
                                                          self.name, text)

    def joining(self):
        self.joined = 1

    def leaving(self):
        self.joined = 0

    def leave(self):
        return self.account.client.perspective.callRemote('leaveGroup',
                                                          self.name)


components.backwardsCompatImplements(TwistedWordsGroup)


class TwistedWordsClient(pb.Referenceable, basesupport.AbstractClientMixin):
    """In some cases, this acts as an Account, since it a source of text
    messages (multiple Words instances may be on a single PB connection)
    """
    def __init__(self, acct, serviceName, perspectiveName, chatui,
                 _logonDeferred=None):
        self.accountName = "%s (%s:%s)" % (acct.accountName, serviceName, perspectiveName)
        self.name = perspectiveName
        print "HELLO I AM A PB SERVICE", serviceName, perspectiveName
        self.chat = chatui
        self.account = acct
        self._logonDeferred = _logonDeferred

    def getPerson(self, name):
        return self.chat.getPerson(name, self)

    def getGroup(self, name):
        return self.chat.getGroup(name, self)

    def getGroupConversation(self, name):
        return self.chat.getGroupConversation(self.getGroup(name))

    def addContact(self, name):
        self.perspective.callRemote('addContact', name)

    def remote_receiveGroupMembers(self, names, group):
        print 'received group members:', names, group
        self.getGroupConversation(group).setGroupMembers(names)

    def remote_receiveGroupMessage(self, sender, group, message, metadata=None):
        print 'received a group message', sender, group, message, metadata
        self.getGroupConversation(group).showGroupMessage(sender, message, metadata)

    def remote_memberJoined(self, member, group):
        print 'member joined', member, group
        self.getGroupConversation(group).memberJoined(member)

    def remote_memberLeft(self, member, group):
        print 'member left'
        self.getGroupConversation(group).memberLeft(member)

    def remote_notifyStatusChanged(self, name, status):
        self.chat.getPerson(name, self).setStatus(status)

    def remote_receiveDirectMessage(self, name, message, metadata=None):
        self.chat.getConversation(self.chat.getPerson(name, self)).showMessage(message, metadata)

    def remote_receiveContactList(self, clist):
        for name, status in clist:
            self.chat.getPerson(name, self).setStatus(status)

    def remote_setGroupMetadata(self, dict_, groupName):
        if dict_.has_key("topic"):
            self.getGroupConversation(groupName).setTopic(dict_["topic"], dict_.get("topic_author", None))

    def joinGroup(self, name):
        self.getGroup(name).joining()
        return self.perspective.callRemote('joinGroup', name).addCallback(self._cbGroupJoined, name)

    def leaveGroup(self, name):
        self.getGroup(name).leaving()
        return self.perspective.callRemote('leaveGroup', name).addCallback(self._cbGroupLeft, name)

    def _cbGroupJoined(self, result, name):
        groupConv = self.chat.getGroupConversation(self.getGroup(name))
        groupConv.showGroupMessage("sys", "you joined")
        self.perspective.callRemote('getGroupMembers', name)

    def _cbGroupLeft(self, result, name):
        print 'left',name
        groupConv = self.chat.getGroupConversation(self.getGroup(name), 1)
        groupConv.showGroupMessage("sys", "you left")

    def connected(self, perspective):
        print 'Connected Words Client!', perspective
        if self._logonDeferred is not None:
            self._logonDeferred.callback(self)
        self.perspective = perspective
        self.chat.getContactsList()


pbFrontEnds = {
    "twisted.words": TwistedWordsClient,
    "twisted.reality": None
    }


class PBAccount(basesupport.AbstractAccount):
    implements(interfaces.IAccount)
    gatewayType = "PB"
    _groupFactory = TwistedWordsGroup
    _personFactory = TwistedWordsPerson

    def __init__(self, accountName, autoLogin, username, password, host, port,
                 services=None):
        """
        @param username: The name of your PB Identity.
        @type username: string
        """
        basesupport.AbstractAccount.__init__(self, accountName, autoLogin,
                                             username, password, host, port)
        self.services = []
        if not services:
            services = [('twisted.words', 'twisted.words', username)]
        for serviceType, serviceName, perspectiveName in services:
            self.services.append([pbFrontEnds[serviceType], serviceName,
                                  perspectiveName])

    def logOn(self, chatui):
        """
        @returns: this breaks with L{interfaces.IAccount}
        @returntype: DeferredList of L{interfaces.IClient}s
        """
        # Overriding basesupport's implementation on account of the
        # fact that _startLogOn tends to return a deferredList rather
        # than a simple Deferred, and we need to do registerAccountClient.
        if (not self._isConnecting) and (not self._isOnline):
            self._isConnecting = 1
            d = self._startLogOn(chatui)
            d.addErrback(self._loginFailed)
            def registerMany(results):
                for success, result in results:
                    if success:
                        chatui.registerAccountClient(result)
                        self._cb_logOn(result)
                    else:
                        log.err(result)
            d.addCallback(registerMany)
            return d
        else:
            raise error.ConnectionError("Connection in progress")


    def _startLogOn(self, chatui):
        print 'Connecting...',
        d = pb.getObjectAt(self.host, self.port)
        d.addCallbacks(self._cbConnected, self._ebConnected,
                       callbackArgs=(chatui,))
        return d

    def _cbConnected(self, root, chatui):
        print 'Connected!'
        print 'Identifying...',
        d = pb.authIdentity(root, self.username, self.password)
        d.addCallbacks(self._cbIdent, self._ebConnected,
                       callbackArgs=(chatui,))
        return d

    def _cbIdent(self, ident, chatui):
        if not ident:
            print 'falsely identified.'
            return self._ebConnected(Failure(Exception("username or password incorrect")))
        print 'Identified!'
        dl = []
        for handlerClass, sname, pname in self.services:
            d = defer.Deferred()
            dl.append(d)
            handler = handlerClass(self, sname, pname, chatui, d)
            ident.callRemote('attach', sname, pname, handler).addCallback(handler.connected)
        return defer.DeferredList(dl)

    def _ebConnected(self, error):
        print 'Not connected.'
        return error

components.backwardsCompatImplements(PBAccount)
