# -*- test-case-name: twisted.web2.dav.test.test_report_expand -*-
##
# Copyright (c) 2006 Apple Computer, Inc. All rights reserved.
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
# 
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
# 
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
#
# DRI: Wilfredo Sanchez, wsanchez@apple.com
##


"""
WebDAV prinicpal-match report
"""

__all__ = ["report_DAV__principal_match"]

from twisted.python import log
from twisted.internet.defer import deferredGenerator, waitForDeferred
from twisted.web2 import responsecode
from twisted.web2.http import StatusResponse, HTTPError
from twisted.web2.dav import davxml
from twisted.web2.dav.davxml import dav_namespace
from twisted.web2.dav.http import ErrorResponse, MultiStatusResponse
from twisted.web2.dav.method import prop_common
from twisted.web2.dav.method.report import NumberOfMatchesWithinLimits
from twisted.web2.dav.method.report import max_number_of_matches
from twisted.web2.dav.resource import isPrincipalResource

def report_DAV__principal_match(self, request, principal_match):
    """
    Generate a principal-match REPORT. (RFC 3744, section 9.3)
    """
    # Verify root element
    if not isinstance(principal_match, davxml.PrincipalMatch):
        raise ValueError("%s expected as root element, not %s."
                         % (davxml.PrincipalMatch.sname(), principal_match.sname()))

    # Only handle Depth: 0
    depth = request.headers.getHeader("depth", "0")
    if depth != "0":
        log.err("Non-zero depth is not allowed: %s" % (depth,))
        raise HTTPError(StatusResponse(responsecode.BAD_REQUEST, "Depth %s not allowed" % (depth,)))
    
    # Get a single DAV:prop element from the REPORT request body
    propertiesForResource = None
    propElement = None
    principalPropElement = None
    lookForPrincipals = True

    for child in principal_match.children:
        if child.qname() == (dav_namespace, "prop"):
            propertiesForResource = prop_common.propertyListForResource
            propElement = child

        elif child.qname() == (dav_namespace, "self"):
            lookForPrincipals = True

        elif child.qname() == (dav_namespace, "principal-property"):
            # Must have one and only one property in this element
            if len(child.children) != 1:
                log.err("Wrong number of properties in DAV:principal-property: %s"
                        % (len(child.children),))
                raise HTTPError(StatusResponse(
                    responsecode.BAD_REQUEST,
                    "DAV:principal-property must contain exactly one property"
                ))

            lookForPrincipals = False
            principalPropElement = child.children[0]

    # Run report for each referenced principal
    try:
        responses = []
        matchcount = 0

        selfPrincipal = self.currentPrincipal(request).children[0]

        # Do some optimisation of access control calculation by determining any inherited ACLs outside of
        # the child resource loop and supply those to the checkPrivileges on each child.
        filteredaces = waitForDeferred(self.inheritedACEsforChildren(request))
        yield filteredaces
        filteredaces = filteredaces.getResult()
    
        children = []
        d = waitForDeferred(self.findChildren("infinity", request, lambda x, y: children.append((x,y)),
                                              privileges=(davxml.Read(),), inherited_aces=filteredaces))
        yield d
        d.getResult()

        if lookForPrincipals:

            for child, uri in children:
                if isPrincipalResource(child) and child.principalMatch(selfPrincipal):
                    # Check size of results is within limit
                    matchcount += 1
                    if matchcount > max_number_of_matches:
                        raise NumberOfMatchesWithinLimits

                    d = waitForDeferred(prop_common.responseForHref(
                        request,
                        responses,
                        davxml.HRef.fromString(uri),
                        child,
                        propertiesForResource,
                        propElement
                    ))
                    yield d
                    d.getResult()
        else:
            for child, uri in children:
                # Try to read the requested property from this resource
                try:
                    prop = waitForDeferred(child.readProperty(principalPropElement.qname(), request))
                    yield prop
                    prop = prop.getResult()
                    if prop: prop.removeWhitespaceNodes()

                    if prop and len(prop.children) == 1 and isinstance(prop.children[0], davxml.HRef):
                        # Find principal associated with this property and test it
                        principal = waitForDeferred(request.locateResource(str(prop.children[0])))
                        yield principal
                        principal = principal.getResult()

                        if principal and isPrincipalResource(principal) and principal.principalMatch(selfPrincipal):
                            # Check size of results is within limit
                            matchcount += 1
                            if matchcount > max_number_of_matches:
                                raise NumberOfMatchesWithinLimits

                            d = waitForDeferred(prop_common.responseForHref(
                                request,
                                responses,
                                davxml.HRef.fromString(uri),
                                child,
                                propertiesForResource,
                                propElement
                            ))
                            yield d
                            d.getResult()
                except HTTPError:
                    # Just ignore a failure to access the property. We treat this like a property that does not exist
                    # or does not match the principal.
                    pass

    except NumberOfMatchesWithinLimits:
        log.err("Too many matching components in principal-match report")
        raise HTTPError(ErrorResponse(
            responsecode.FORBIDDEN,
            (dav_namespace, "number-of-matches-within-limits")
        ))

    yield MultiStatusResponse(responses)

report_DAV__principal_match = deferredGenerator(report_DAV__principal_match)