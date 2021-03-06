# flickrpc -- a Flickr client library.
#
# Copyright (C) 2007 Ross Burton <ross@burtonini.com>
#
# This program is free software; you can redistribute it and/or modify it under
# the terms of the GNU Lesser General Public License as published by the Free
# Software Foundation; either version 2, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE.  See the GNU Lesser General Public License for more
# details.
#
# You should have received a copy of the GNU General Public License along with
# this program; if not, write to the Free Software Foundation, Inc., 51 Franklin
# St, Fifth Floor, Boston, MA 02110-1301 USA

import logging, os, mimetools, urllib
import gio
from twisted.internet import defer
from twisted.python.failure import Failure
import proxyclient as client

try:
    from hashlib import md5
except ImportError:
    from md5 import md5

try:
    from xml.etree import ElementTree
except ImportError:
    from elementtree import ElementTree

class FlickrError(Exception):
    def __init__(self, code, message):
        Exception.__init__(self)
        self.code = int(code)
        self.message = message
    
    def __str__(self):
        return "%d: %s" % (self.code, self.message)

(SIZE_SQUARE,
 SIZE_THUMB,
 SIZE_SMALL,
 SIZE_MEDIUM,
 SIZE_LARGE) = range (0, 5)

class Flickr:
    endpoint = "http://www.yupoo.com/api/rest/"
    
    def __init__(self, api_key, secret, perms="read"):
        self.__methods = {}
        self.api_key = api_key
        self.secret = secret
        self.perms = perms
        self.token = None
        self.logger = logging.getLogger('flickrest')
        self.set_proxy(os.environ.get("http_proxy", None))
        self.fullname = None
        self.username = None
        self.nsid = None

    def get_fullname(self):
        return self.fullname

    def get_username(self):
        return self.username

    def get_nsid(self):
        return self.nsid

    def set_proxy(self, proxy):
        # Handle proxies which are not URLs
        if proxy and "://" not in proxy:
            proxy = "http://" + proxy
        self.proxy = proxy
    
    def __repr__(self):
        return "<FlickREST>"
    
    def __getTokenFile(self):
        """Get the filename that contains the authentication token for the API key"""
        return os.path.expanduser(os.path.join("~", ".flickr", self.api_key, "auth.xml"))

    def clear_cached(self):
        """Remove any cached information on disk."""
        self.fullname = None
        self.username = None
        self.nsid = None
        token = self.__getTokenFile()
        if os.path.exists(token):
            os.remove(token)
        self.token = None
    
    def __sign(self, kwargs):
        kwargs['api_key'] = self.api_key
        # If authenticating we don't yet have a token
        if self.token:
            kwargs['auth_token'] = self.token
        # I know this is less efficient than working with lists, but this is
        # much more readable.
        sig = reduce(lambda sig, key: sig + key + str(kwargs[key]),
                     sorted(kwargs.keys()),
                     self.secret)
        kwargs['api_sig'] = md5(sig).hexdigest()

    def __call(self, method, kwargs):
        kwargs["method"] = method
        self.__sign(kwargs)
        self.logger.info("Calling %s" % method)
        return client.getPage(Flickr.endpoint, proxy=self.proxy, method="POST",
                              headers={"Content-Type": "application/x-www-form-urlencoded"},
                              postdata=urllib.urlencode(kwargs))
    
    def __cb(self, data, method):
        self.logger.info("%s returned" % method)
        xml = ElementTree.XML(data)
        if xml.tag == "rsp" and xml.get("stat") == "ok":
            return xml
        elif xml.tag == "rsp" and xml.get("stat") == "fail":
            err = xml.find("err")
            raise FlickrError(err.get("code"), err.get("msg"))
        else:
            # Fake an error in this case
            raise FlickrError(0, "Invalid response")
    
    def __getattr__(self, method):
        method = "yupoo." + method.replace("_", ".")
        if not self.__methods.has_key(method):
            def proxy(method=method, **kwargs):
                return self.__call(method, kwargs).addCallback(self.__cb, method)
            self.__methods[method] = proxy
        return self.__methods[method]

    @staticmethod
    def __encodeForm(inputs):
        """
        Takes a dict of inputs and returns a multipart/form-data string
        containing the utf-8 encoded data. Keys must be strings, values
        can be either strings or file-like objects.
        """
        boundary = mimetools.choose_boundary()
        lines = []
        for key, val in inputs.items():
            lines.append("--" + boundary.encode("utf-8"))
            header = 'Content-Disposition: form-data; name="%s";' % key
            if isinstance(val, gio.File):
                header += 'filename="%s";' % val.get_basename()
                lines.append(header)
                header = "Content-Type: application/octet-stream"
            lines.append(header)
            lines.append("")
            if isinstance(val, gio.File):
                contents, length, etags = val.load_contents()
                lines.append(contents)
            # Otherwise just hope it is string-like and encode it to
            # UTF-8. TODO: this breaks when val is binary data.
            else:
                lines.append(str(val).encode('utf-8'))
        # Add final boundary.
        lines.append("--" + boundary.encode("utf-8"))
        return (boundary, '\r\n'.join(lines))
    
    def upload(self, uri=None, imageData=None,
               title=None, desc=None, tags=None,
               is_public=None, is_family=None, is_friend=None,
               safety=None, search_hidden=None, content_type=None,
               progress_tracker=None):
        # Sanity check the arguments
        if uri is None and imageData is None:
            raise ValueError("Need to pass either uri or imageData")
        if uri and imageData:
            raise ValueError("Cannot pass both uri and imageData")

        kwargs = {}
        if title:
            kwargs['title'] = title
        if desc:
            kwargs['description'] = desc
        if tags:
            kwargs['tags'] = tags
        if is_public is not None:
            kwargs['is_public'] = is_public and 1 or 0
        if is_family is not None:
            kwargs['is_family'] = is_family and 1 or 0
        if is_friend is not None:
            kwargs['is_friend'] = is_friend and 1 or 0
        if safety:
            kwargs['safety_level'] = safety
        if search_hidden is not None:
            kwargs['hidden'] = search_hidden and 2 or 1 # Why Flickr, why?
        if content_type:
            kwargs['content_type'] = content_type
        self.__sign(kwargs)
        self.logger.info("Upload args %s" % kwargs)
        
        if imageData:
            kwargs['photo'] = imageData
        else:
            kwargs['photo'] = gio.File(uri)

        (boundary, form) = self.__encodeForm(kwargs)
        headers= {
            "Content-Type": "multipart/form-data; boundary=%s" % boundary,
            "Content-Length": str(len(form))
            }

        self.logger.info("Calling upload")
        return client.upload("http://www.yupoo.com/api/upload/",
                             proxy=self.proxy, method="POST",
                             headers=headers, postdata=form,
                             progress_tracker=progress_tracker).addCallback(self.__cb, "upload")

    def authenticate_2(self, state):
        def gotToken(e):
            # Set the token
            self.token = e.find("auth/token").text

            # Pulling out the user information
            user = e.find("auth/user")
            # Setting the user variables
            self.fullname = user.get("fullname")
            self.username = user.get("username")
            self.nsid = user.get("nsid")
            self.logger.debug("the nsid is %s", self.nsid)

            # Cache the authentication
            filename = self.__getTokenFile()
            path = os.path.dirname(filename)
            if not os.path.exists(path):
                os.makedirs(path, 0700)
            f = file(filename, "w")
            f.write(ElementTree.tostring(e))
            f.close()

            # Callback to the user
            return True
        return self.auth_getToken(frob=state['frob']).addCallback(gotToken)

    def __get_frob(self):
        """Make the getFrob() call."""
        def gotFrob(xml):
            frob = xml.find("frob").text
            keys = { 'perms': self.perms,
                     'frob': frob }
            self.__sign(keys)
            url = "http://www.yupoo.com/services/auth/?api_key=%(api_key)s&perms=%(perms)s&frob=%(frob)s&api_sig=%(api_sig)s" % keys
            return {'url': url, 'frob': frob}
        return self.auth_getFrob().addCallback(gotFrob)

    def authenticate_1(self):
        """Attempts to log in to Flickr. The return value is a Twisted Deferred
        object that callbacks when the first part of the authentication is
        completed.  If the result passed to the deferred callback is None, then
        the required authentication was locally cached and you are
        authenticated.  Otherwise the result is a dictionary, you should open
        the URL specified by the 'url' key and instruct the user to follow the
        instructions.  Once that is done, pass the state to
        flickrest.authenticate_2()."""

        filename = self.__getTokenFile()
        if os.path.exists(filename):
            try:
                e = ElementTree.parse(filename).getroot()
                self.token = e.find("auth/token").text
                
                user = e.find("auth/user")
                self.fullname = user.get("fullname")
                self.username = user.get("username")
                self.nsid = user.get("nsid")

                def reply(xml):
                    return defer.succeed(None)
                def failed(failure):
                    # If checkToken() failed, we need to re-authenticate
                    self.clear_cached()
                    return self.__get_frob()
                return self.auth_checkToken().addCallbacks(reply, failed)
            except:
                # TODO: print the exception to stderr?
                pass
            
        return self.__get_frob()
    
    @staticmethod
    def get_photo_url(photo, size=SIZE_MEDIUM):
        if photo is None:
            return None

        # Handle medium as the default
        suffix = ""
        if size == SIZE_SQUARE:
            suffix = "_s"
        elif size == SIZE_THUMB:
            suffix = "_t"
        elif size == SIZE_SMALL:
            suffix = "_m"
        elif size == SIZE_LARGE:
            suffix = "_b"

        return "http://static.flickr.com/%s/%s_%s%s.jpg" % (photo.get("server"), photo.get("id"), photo.get("secret"), suffix)
