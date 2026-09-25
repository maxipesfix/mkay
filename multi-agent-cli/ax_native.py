"""Shared native macOS accessibility (AX) binding for every backend.

AXUIElement references are retained while a snapshot is read. A removed element
invalidates the snapshot; it never turns into a different element at an index.
Requires only Python's stdlib: public AX/CoreFoundation APIs through ctypes.
"""
import ctypes as C
import sys
import time


class SidebarError(RuntimeError):
    pass


class Changed(SidebarError):
    pass


class AXError(SidebarError):
    def __init__(self, operation, code):
        self.code = code
        message = f"Native accessibility {operation} failed ({code})."
        if operation == 'press':
            message += ' Check the app before retrying; the action may already have occurred.'
        super().__init__(message)


class Element:
    def __init__(self, api, ref):
        self.api, self.ref = api, ref
        self._attributes = None

    def __del__(self):
        self.api.cf.CFRelease(self.ref)

    def __hash__(self):
        return self.api.cf.CFHash(self.ref)

    def __eq__(self, other):
        return isinstance(other, Element) and bool(self.api.cf.CFEqual(self.ref, other.ref))

    def get(self, name):
        return self.api.get(self.ref, name)

    def supports(self, name):
        if self._attributes is None:
            self._attributes = frozenset(self.api.attribute_names(self.ref))
        return name in self._attributes

    def press(self):
        self.api.press(self.ref)

    def perform(self, action_name):
        self.api.perform(self.ref, action_name)

    def set_bool(self, name, flag=True):
        return self.api.set_bool(self.ref, name, flag)


class NativeAX:
    """Small ownership-aware binding to public macOS AX/CoreFoundation APIs."""
    UTF8 = 0x08000100

    def __init__(self):
        self.cf = C.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
        self.ax = C.CDLL('/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices')
        ptr = C.c_void_p
        signatures = {
            'CFRelease': (None, [ptr]), 'CFRetain': (ptr, [ptr]),
            'CFGetTypeID': (C.c_ulong, [ptr]), 'CFHash': (C.c_ulong, [ptr]),
            'CFEqual': (C.c_bool, [ptr, ptr]),
            'CFStringCreateWithCString': (ptr, [ptr, C.c_char_p, C.c_uint32]),
            'CFStringGetLength': (C.c_long, [ptr]),
            'CFStringGetCString': (C.c_bool, [ptr, ptr, C.c_long, C.c_uint32]),
            'CFArrayGetCount': (C.c_long, [ptr]),
            'CFArrayGetValueAtIndex': (ptr, [ptr, C.c_long]),
            'CFBooleanGetValue': (C.c_bool, [ptr]),
            'CFNumberGetValue': (C.c_bool, [ptr, C.c_int, ptr]),
        }
        for name in ('CFString', 'CFArray', 'CFBoolean', 'CFNumber'):
            signatures[name + 'GetTypeID'] = (C.c_ulong, [])
        for name, (result, args) in signatures.items():
            fn = getattr(self.cf, name)
            fn.restype, fn.argtypes = result, args
        signatures = {
            'AXUIElementGetTypeID': (C.c_ulong, []),
            'AXIsProcessTrusted': (C.c_bool, []),
            'AXUIElementCreateApplication': (ptr, [C.c_int]),
            'AXUIElementSetMessagingTimeout': (C.c_int, [ptr, C.c_float]),
            'AXUIElementCopyAttributeValue': (C.c_int, [ptr, ptr, C.POINTER(ptr)]),
            'AXUIElementCopyAttributeNames': (C.c_int, [ptr, C.POINTER(ptr)]),
            'AXUIElementSetAttributeValue': (C.c_int, [ptr, ptr, ptr]),
            'AXUIElementPerformAction': (C.c_int, [ptr, ptr]),
        }
        for name, (result, args) in signatures.items():
            fn = getattr(self.ax, name)
            fn.restype, fn.argtypes = result, args
        self.types = {name: getattr(self.cf, name + 'GetTypeID')()
                      for name in ('CFString', 'CFArray', 'CFBoolean', 'CFNumber')}
        self.types['AX'] = self.ax.AXUIElementGetTypeID()

    def string(self, text):
        ref = self.cf.CFStringCreateWithCString(None, text.encode('utf-8'), self.UTF8)
        if not ref:
            raise SidebarError('Could not allocate an accessibility attribute name.')
        return ref

    def decode(self, ref):
        if not ref:
            return None
        kind = self.cf.CFGetTypeID(ref)
        if kind == self.types['CFString']:
            buffer = C.create_string_buffer(self.cf.CFStringGetLength(ref) * 4 + 1)
            if not self.cf.CFStringGetCString(ref, buffer, len(buffer), self.UTF8):
                raise SidebarError('Could not decode accessibility text.')
            return buffer.value.decode('utf-8')
        if kind == self.types['CFBoolean']:
            return bool(self.cf.CFBooleanGetValue(ref))
        if kind == self.types['CFNumber']:
            value = C.c_longlong()
            if not self.cf.CFNumberGetValue(ref, 4, C.byref(value)):
                raise SidebarError('Could not decode accessibility number.')
            return value.value
        if kind == self.types['CFArray']:
            return [self.decode(self.cf.CFArrayGetValueAtIndex(ref, i))
                    for i in range(self.cf.CFArrayGetCount(ref))]
        if kind == self.types['AX']:
            return Element(self, self.cf.CFRetain(ref))
        raise SidebarError(f'Unsupported accessibility value type: {kind}.')

    def get(self, element, name):
        key, value = self.string(name), C.c_void_p()
        try:
            code = self.ax.AXUIElementCopyAttributeValue(element, key, C.byref(value))
            if code in (-25205, -25212):  # Unsupported optional attribute / no value.
                return None
            if code:
                raise AXError('read ' + name, code)
            return self.decode(value.value)
        finally:
            self.cf.CFRelease(key)
            if value.value:
                self.cf.CFRelease(value.value)

    def attribute_names(self, element):
        value = C.c_void_p()
        try:
            code = self.ax.AXUIElementCopyAttributeNames(element, C.byref(value))
            if code:
                raise AXError('read supported attributes', code)
            names = self.decode(value.value)
            if not isinstance(names, list) or not all(isinstance(name, str) for name in names):
                raise SidebarError('Unexpected accessibility attribute-name list.')
            return names
        finally:
            if value.value:
                self.cf.CFRelease(value.value)

    def perform(self, element, action_name):
        action = self.string(action_name)
        try:
            code = self.ax.AXUIElementPerformAction(element, action)
            if code:
                raise AXError('press' if action_name == 'AXPress' else 'perform ' + action_name, code)
        finally:
            self.cf.CFRelease(action)

    def press(self, element):
        self.perform(element, 'AXPress')

    def set_bool(self, element, name, flag=True):
        """Set a boolean attribute; return the raw AX status code for the caller."""
        key = self.string(name)
        try:
            value = C.c_void_p.in_dll(self.cf, 'kCFBooleanTrue' if flag else 'kCFBooleanFalse').value
            return self.ax.AXUIElementSetAttributeValue(element, key, value)
        finally:
            self.cf.CFRelease(key)

    def trusted(self):
        return bool(self.ax.AXIsProcessTrusted())

    def application(self, pid, flag='AXEnhancedUserInterface'):
        """Return the app element with its full Electron tree exposed.

        Claude and ChatGPT honor AXEnhancedUserInterface. Cursor rejects it as
        not implemented (-25208) and needs AXManualAccessibility instead.
        """
        ref = self.ax.AXUIElementCreateApplication(pid)
        if not ref:
            raise SidebarError('Could not create the application accessibility reference.')
        app = Element(self, ref)
        code = self.ax.AXUIElementSetMessagingTimeout(ref, 1.0)
        if code:
            raise AXError('set messaging timeout', code)
        if app.get(flag) not in (True, 1):
            code = self.set_bool(ref, flag)
            if code == -25208 and flag != 'AXManualAccessibility':
                # Not implemented: newer Electron builds (Cursor; Claude 2.9939) take only this flag.
                code = self.set_bool(ref, 'AXManualAccessibility')
            if code not in (0, -25205):
                raise AXError('enable enhanced accessibility', code)
        return app


# A control role does not guarantee a leaf: buttons and links can wrap groups
# containing other controls. Only omit the content of text/image/editor leaves.
LEAVES = {'AXStaticText', 'AXImage', 'AXTextArea'}


def label(node):
    for key in ('AXTitle', 'AXDescription', 'AXValue'):
        value = node.get(key)
        if isinstance(value, str) and value:
            return value
    return ''


def expanded(node):
    # Some native providers answer an unadvertised AXExpanded with a default
    # false. That does not make every ordinary session button a section header.
    if not node.supports('AXExpanded'):
        return None
    value = node.get('AXExpanded')
    if value in (True, 1, 'true', '1'):
        return True
    if value in (False, 0, 'false', '0'):
        return False
    return None


class Walker:
    """Deadline-bounded depth-first traversal that retains native references."""
    # Default budgets fit the Claude and ChatGPT sidebars; larger trees pass their own.
    STACK_BUDGET = 3000
    # Roles whose subtrees are never needed, such as an IDE file explorer.
    PRUNE = frozenset()

    def __init__(self, app, seconds=70, log=None):
        self.app = app
        self.deadline = time.monotonic() + seconds
        self.log = log or (lambda message: print(message, file=sys.stderr, flush=True))

    def walk(self, root, limit):
        # The ancestry comes from this traversal; never ask AXParent to rebuild
        # a System Events object specifier. Retain native references in the stack.
        stack, seen = [(root, ())], set()
        while stack:
            if time.monotonic() >= self.deadline or len(seen) >= limit:
                raise SidebarError('Sidebar scan exceeded its limit; no incomplete list returned.')
            node, ancestors = stack.pop()
            if node in seen:
                continue
            seen.add(node)
            role = node.get('AXRole')
            if not role:
                raise Changed('A sidebar control disappeared during reading.')
            yield node, role, ancestors
            if role not in LEAVES and role not in self.PRUNE:
                children = node.get('AXChildren') or []
                if not isinstance(children, list):
                    raise SidebarError('Unexpected accessibility children value.')
                if len(stack) + len(children) > self.STACK_BUDGET:
                    raise SidebarError('Sidebar scan exceeded its control budget.')
                stack.extend((child, ancestors + (node,)) for child in reversed(children))

    def read(self, operation):
        # Retry only reads with fresh handles. Never retry a press.
        for attempt in range(3):
            try:
                return operation()
            except (Changed, AXError) as error:
                if isinstance(error, AXError) and error.code not in (-25202, -25204):
                    raise
                if attempt == 2:
                    raise
                time.sleep(0.15)

    def wait_read(self, operation):
        for attempt in range(20):
            try:
                return operation()
            except (Changed, AXError) as error:
                if isinstance(error, AXError) and error.code not in (-25202, -25204):
                    raise
                if attempt == 19 or time.monotonic() >= self.deadline:
                    raise
                time.sleep(0.2)

    @staticmethod
    def unique(items, description):
        if len(items) != 1:
            raise SidebarError(f'Expected one {description}; found {len(items)}.')
        return items[0]
