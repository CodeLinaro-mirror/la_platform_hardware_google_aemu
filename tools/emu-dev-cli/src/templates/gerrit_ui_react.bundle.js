// AUTO-GENERATED from src/ui/gerrit/*.tsx via 'bazel build @aemu//tools/emu-dev-cli:gerrit_ui_bundle'.
// Do not edit manually; run src/ui/gerrit/build_bundle.py to refresh.
"use strict";
(() => {
  var __create = Object.create;
  var __defProp = Object.defineProperty;
  var __getOwnPropDesc = Object.getOwnPropertyDescriptor;
  var __getOwnPropNames = Object.getOwnPropertyNames;
  var __getProtoOf = Object.getPrototypeOf, __hasOwnProp = Object.prototype.hasOwnProperty;
  var __commonJS = (cb, mod) => function() {
    try {
      return mod || (0, cb[__getOwnPropNames(cb)[0]])((mod = { exports: {} }).exports, mod), mod.exports;
    } catch (e) {
      throw mod = 0, e;
    }
  };
  var __copyProps = (to, from, except, desc) => {
    if (from && typeof from == "object" || typeof from == "function")
      for (let key of __getOwnPropNames(from))
        !__hasOwnProp.call(to, key) && key !== except && __defProp(to, key, { get: () => from[key], enumerable: !(desc = __getOwnPropDesc(from, key)) || desc.enumerable });
    return to;
  };
  var __toESM = (mod, isNodeMode, target) => (target = mod != null ? __create(__getProtoOf(mod)) : {}, __copyProps(
    // If the importer is in node compatibility mode or this is not an ESM
    // file that has been converted to a CommonJS file using a Babel-
    // compatible transform (i.e. "__esModule" has not been set), then set
    // "default" to the CommonJS "module.exports" for node compatibility.
    isNodeMode || !mod || !mod.__esModule ? __defProp(target, "default", { value: mod, enumerable: !0 }) : target,
    mod
  ));

  // ../../../../../../../../execroot/_main/bazel-out/k8-fastbuild-ST-f481a530da89/bin/external/aemu+/api/web/node_modules/.aspect_rules_js/react@19.2.7/node_modules/react/cjs/react.production.js
  var require_react_production = __commonJS({
    "../../../../../../../../execroot/_main/bazel-out/k8-fastbuild-ST-f481a530da89/bin/external/aemu+/api/web/node_modules/.aspect_rules_js/react@19.2.7/node_modules/react/cjs/react.production.js"(exports) {
      "use strict";
      var REACT_ELEMENT_TYPE = /* @__PURE__ */ Symbol.for("react.transitional.element"), REACT_PORTAL_TYPE = /* @__PURE__ */ Symbol.for("react.portal"), REACT_FRAGMENT_TYPE = /* @__PURE__ */ Symbol.for("react.fragment"), REACT_STRICT_MODE_TYPE = /* @__PURE__ */ Symbol.for("react.strict_mode"), REACT_PROFILER_TYPE = /* @__PURE__ */ Symbol.for("react.profiler"), REACT_CONSUMER_TYPE = /* @__PURE__ */ Symbol.for("react.consumer"), REACT_CONTEXT_TYPE = /* @__PURE__ */ Symbol.for("react.context"), REACT_FORWARD_REF_TYPE = /* @__PURE__ */ Symbol.for("react.forward_ref"), REACT_SUSPENSE_TYPE = /* @__PURE__ */ Symbol.for("react.suspense"), REACT_MEMO_TYPE = /* @__PURE__ */ Symbol.for("react.memo"), REACT_LAZY_TYPE = /* @__PURE__ */ Symbol.for("react.lazy"), REACT_ACTIVITY_TYPE = /* @__PURE__ */ Symbol.for("react.activity"), MAYBE_ITERATOR_SYMBOL = Symbol.iterator;
      function getIteratorFn(maybeIterable) {
        return maybeIterable === null || typeof maybeIterable != "object" ? null : (maybeIterable = MAYBE_ITERATOR_SYMBOL && maybeIterable[MAYBE_ITERATOR_SYMBOL] || maybeIterable["@@iterator"], typeof maybeIterable == "function" ? maybeIterable : null);
      }
      var ReactNoopUpdateQueue = {
        isMounted: function() {
          return !1;
        },
        enqueueForceUpdate: function() {
        },
        enqueueReplaceState: function() {
        },
        enqueueSetState: function() {
        }
      }, assign = Object.assign, emptyObject = {};
      function Component(props, context, updater) {
        this.props = props, this.context = context, this.refs = emptyObject, this.updater = updater || ReactNoopUpdateQueue;
      }
      Component.prototype.isReactComponent = {};
      Component.prototype.setState = function(partialState, callback) {
        if (typeof partialState != "object" && typeof partialState != "function" && partialState != null)
          throw Error(
            "takes an object of state variables to update or a function which returns an object of state variables."
          );
        this.updater.enqueueSetState(this, partialState, callback, "setState");
      };
      Component.prototype.forceUpdate = function(callback) {
        this.updater.enqueueForceUpdate(this, callback, "forceUpdate");
      };
      function ComponentDummy() {
      }
      ComponentDummy.prototype = Component.prototype;
      function PureComponent(props, context, updater) {
        this.props = props, this.context = context, this.refs = emptyObject, this.updater = updater || ReactNoopUpdateQueue;
      }
      var pureComponentPrototype = PureComponent.prototype = new ComponentDummy();
      pureComponentPrototype.constructor = PureComponent;
      assign(pureComponentPrototype, Component.prototype);
      pureComponentPrototype.isPureReactComponent = !0;
      var isArrayImpl = Array.isArray;
      function noop() {
      }
      var ReactSharedInternals = { H: null, A: null, T: null, S: null }, hasOwnProperty = Object.prototype.hasOwnProperty;
      function ReactElement(type, key, props) {
        var refProp = props.ref;
        return {
          $$typeof: REACT_ELEMENT_TYPE,
          type,
          key,
          ref: refProp !== void 0 ? refProp : null,
          props
        };
      }
      function cloneAndReplaceKey(oldElement, newKey) {
        return ReactElement(oldElement.type, newKey, oldElement.props);
      }
      function isValidElement(object) {
        return typeof object == "object" && object !== null && object.$$typeof === REACT_ELEMENT_TYPE;
      }
      function escape(key) {
        var escaperLookup = { "=": "=0", ":": "=2" };
        return "$" + key.replace(/[=:]/g, function(match) {
          return escaperLookup[match];
        });
      }
      var userProvidedKeyEscapeRegex = /\/+/g;
      function getElementKey(element, index) {
        return typeof element == "object" && element !== null && element.key != null ? escape("" + element.key) : index.toString(36);
      }
      function resolveThenable(thenable) {
        switch (thenable.status) {
          case "fulfilled":
            return thenable.value;
          case "rejected":
            throw thenable.reason;
          default:
            switch (typeof thenable.status == "string" ? thenable.then(noop, noop) : (thenable.status = "pending", thenable.then(
              function(fulfilledValue) {
                thenable.status === "pending" && (thenable.status = "fulfilled", thenable.value = fulfilledValue);
              },
              function(error) {
                thenable.status === "pending" && (thenable.status = "rejected", thenable.reason = error);
              }
            )), thenable.status) {
              case "fulfilled":
                return thenable.value;
              case "rejected":
                throw thenable.reason;
            }
        }
        throw thenable;
      }
      function mapIntoArray(children, array, escapedPrefix, nameSoFar, callback) {
        var type = typeof children;
        (type === "undefined" || type === "boolean") && (children = null);
        var invokeCallback = !1;
        if (children === null) invokeCallback = !0;
        else
          switch (type) {
            case "bigint":
            case "string":
            case "number":
              invokeCallback = !0;
              break;
            case "object":
              switch (children.$$typeof) {
                case REACT_ELEMENT_TYPE:
                case REACT_PORTAL_TYPE:
                  invokeCallback = !0;
                  break;
                case REACT_LAZY_TYPE:
                  return invokeCallback = children._init, mapIntoArray(
                    invokeCallback(children._payload),
                    array,
                    escapedPrefix,
                    nameSoFar,
                    callback
                  );
              }
          }
        if (invokeCallback)
          return callback = callback(children), invokeCallback = nameSoFar === "" ? "." + getElementKey(children, 0) : nameSoFar, isArrayImpl(callback) ? (escapedPrefix = "", invokeCallback != null && (escapedPrefix = invokeCallback.replace(userProvidedKeyEscapeRegex, "$&/") + "/"), mapIntoArray(callback, array, escapedPrefix, "", function(c) {
            return c;
          })) : callback != null && (isValidElement(callback) && (callback = cloneAndReplaceKey(
            callback,
            escapedPrefix + (callback.key == null || children && children.key === callback.key ? "" : ("" + callback.key).replace(
              userProvidedKeyEscapeRegex,
              "$&/"
            ) + "/") + invokeCallback
          )), array.push(callback)), 1;
        invokeCallback = 0;
        var nextNamePrefix = nameSoFar === "" ? "." : nameSoFar + ":";
        if (isArrayImpl(children))
          for (var i = 0; i < children.length; i++)
            nameSoFar = children[i], type = nextNamePrefix + getElementKey(nameSoFar, i), invokeCallback += mapIntoArray(
              nameSoFar,
              array,
              escapedPrefix,
              type,
              callback
            );
        else if (i = getIteratorFn(children), typeof i == "function")
          for (children = i.call(children), i = 0; !(nameSoFar = children.next()).done; )
            nameSoFar = nameSoFar.value, type = nextNamePrefix + getElementKey(nameSoFar, i++), invokeCallback += mapIntoArray(
              nameSoFar,
              array,
              escapedPrefix,
              type,
              callback
            );
        else if (type === "object") {
          if (typeof children.then == "function")
            return mapIntoArray(
              resolveThenable(children),
              array,
              escapedPrefix,
              nameSoFar,
              callback
            );
          throw array = String(children), Error(
            "Objects are not valid as a React child (found: " + (array === "[object Object]" ? "object with keys {" + Object.keys(children).join(", ") + "}" : array) + "). If you meant to render a collection of children, use an array instead."
          );
        }
        return invokeCallback;
      }
      function mapChildren(children, func, context) {
        if (children == null) return children;
        var result = [], count = 0;
        return mapIntoArray(children, result, "", "", function(child) {
          return func.call(context, child, count++);
        }), result;
      }
      function lazyInitializer(payload) {
        if (payload._status === -1) {
          var ctor = payload._result;
          ctor = ctor(), ctor.then(
            function(moduleObject) {
              (payload._status === 0 || payload._status === -1) && (payload._status = 1, payload._result = moduleObject);
            },
            function(error) {
              (payload._status === 0 || payload._status === -1) && (payload._status = 2, payload._result = error);
            }
          ), payload._status === -1 && (payload._status = 0, payload._result = ctor);
        }
        if (payload._status === 1) return payload._result.default;
        throw payload._result;
      }
      var reportGlobalError = typeof reportError == "function" ? reportError : function(error) {
        if (typeof window == "object" && typeof window.ErrorEvent == "function") {
          var event = new window.ErrorEvent("error", {
            bubbles: !0,
            cancelable: !0,
            message: typeof error == "object" && error !== null && typeof error.message == "string" ? String(error.message) : String(error),
            error
          });
          if (!window.dispatchEvent(event)) return;
        } else if (typeof process == "object" && typeof process.emit == "function") {
          process.emit("uncaughtException", error);
          return;
        }
        console.error(error);
      }, Children = {
        map: mapChildren,
        forEach: function(children, forEachFunc, forEachContext) {
          mapChildren(
            children,
            function() {
              forEachFunc.apply(this, arguments);
            },
            forEachContext
          );
        },
        count: function(children) {
          var n = 0;
          return mapChildren(children, function() {
            n++;
          }), n;
        },
        toArray: function(children) {
          return mapChildren(children, function(child) {
            return child;
          }) || [];
        },
        only: function(children) {
          if (!isValidElement(children))
            throw Error(
              "React.Children.only expected to receive a single React element child."
            );
          return children;
        }
      };
      exports.Activity = REACT_ACTIVITY_TYPE;
      exports.Children = Children;
      exports.Component = Component;
      exports.Fragment = REACT_FRAGMENT_TYPE;
      exports.Profiler = REACT_PROFILER_TYPE;
      exports.PureComponent = PureComponent;
      exports.StrictMode = REACT_STRICT_MODE_TYPE;
      exports.Suspense = REACT_SUSPENSE_TYPE;
      exports.__CLIENT_INTERNALS_DO_NOT_USE_OR_WARN_USERS_THEY_CANNOT_UPGRADE = ReactSharedInternals;
      exports.__COMPILER_RUNTIME = {
        __proto__: null,
        c: function(size) {
          return ReactSharedInternals.H.useMemoCache(size);
        }
      };
      exports.cache = function(fn) {
        return function() {
          return fn.apply(null, arguments);
        };
      };
      exports.cacheSignal = function() {
        return null;
      };
      exports.cloneElement = function(element, config, children) {
        if (element == null)
          throw Error(
            "The argument must be a React element, but you passed " + element + "."
          );
        var props = assign({}, element.props), key = element.key;
        if (config != null)
          for (propName in config.key !== void 0 && (key = "" + config.key), config)
            !hasOwnProperty.call(config, propName) || propName === "key" || propName === "__self" || propName === "__source" || propName === "ref" && config.ref === void 0 || (props[propName] = config[propName]);
        var propName = arguments.length - 2;
        if (propName === 1) props.children = children;
        else if (1 < propName) {
          for (var childArray = Array(propName), i = 0; i < propName; i++)
            childArray[i] = arguments[i + 2];
          props.children = childArray;
        }
        return ReactElement(element.type, key, props);
      };
      exports.createContext = function(defaultValue) {
        return defaultValue = {
          $$typeof: REACT_CONTEXT_TYPE,
          _currentValue: defaultValue,
          _currentValue2: defaultValue,
          _threadCount: 0,
          Provider: null,
          Consumer: null
        }, defaultValue.Provider = defaultValue, defaultValue.Consumer = {
          $$typeof: REACT_CONSUMER_TYPE,
          _context: defaultValue
        }, defaultValue;
      };
      exports.createElement = function(type, config, children) {
        var propName, props = {}, key = null;
        if (config != null)
          for (propName in config.key !== void 0 && (key = "" + config.key), config)
            hasOwnProperty.call(config, propName) && propName !== "key" && propName !== "__self" && propName !== "__source" && (props[propName] = config[propName]);
        var childrenLength = arguments.length - 2;
        if (childrenLength === 1) props.children = children;
        else if (1 < childrenLength) {
          for (var childArray = Array(childrenLength), i = 0; i < childrenLength; i++)
            childArray[i] = arguments[i + 2];
          props.children = childArray;
        }
        if (type && type.defaultProps)
          for (propName in childrenLength = type.defaultProps, childrenLength)
            props[propName] === void 0 && (props[propName] = childrenLength[propName]);
        return ReactElement(type, key, props);
      };
      exports.createRef = function() {
        return { current: null };
      };
      exports.forwardRef = function(render) {
        return { $$typeof: REACT_FORWARD_REF_TYPE, render };
      };
      exports.isValidElement = isValidElement;
      exports.lazy = function(ctor) {
        return {
          $$typeof: REACT_LAZY_TYPE,
          _payload: { _status: -1, _result: ctor },
          _init: lazyInitializer
        };
      };
      exports.memo = function(type, compare) {
        return {
          $$typeof: REACT_MEMO_TYPE,
          type,
          compare: compare === void 0 ? null : compare
        };
      };
      exports.startTransition = function(scope) {
        var prevTransition = ReactSharedInternals.T, currentTransition = {};
        ReactSharedInternals.T = currentTransition;
        try {
          var returnValue = scope(), onStartTransitionFinish = ReactSharedInternals.S;
          onStartTransitionFinish !== null && onStartTransitionFinish(currentTransition, returnValue), typeof returnValue == "object" && returnValue !== null && typeof returnValue.then == "function" && returnValue.then(noop, reportGlobalError);
        } catch (error) {
          reportGlobalError(error);
        } finally {
          prevTransition !== null && currentTransition.types !== null && (prevTransition.types = currentTransition.types), ReactSharedInternals.T = prevTransition;
        }
      };
      exports.unstable_useCacheRefresh = function() {
        return ReactSharedInternals.H.useCacheRefresh();
      };
      exports.use = function(usable) {
        return ReactSharedInternals.H.use(usable);
      };
      exports.useActionState = function(action, initialState, permalink) {
        return ReactSharedInternals.H.useActionState(action, initialState, permalink);
      };
      exports.useCallback = function(callback, deps) {
        return ReactSharedInternals.H.useCallback(callback, deps);
      };
      exports.useContext = function(Context) {
        return ReactSharedInternals.H.useContext(Context);
      };
      exports.useDebugValue = function() {
      };
      exports.useDeferredValue = function(value, initialValue) {
        return ReactSharedInternals.H.useDeferredValue(value, initialValue);
      };
      exports.useEffect = function(create, deps) {
        return ReactSharedInternals.H.useEffect(create, deps);
      };
      exports.useEffectEvent = function(callback) {
        return ReactSharedInternals.H.useEffectEvent(callback);
      };
      exports.useId = function() {
        return ReactSharedInternals.H.useId();
      };
      exports.useImperativeHandle = function(ref, create, deps) {
        return ReactSharedInternals.H.useImperativeHandle(ref, create, deps);
      };
      exports.useInsertionEffect = function(create, deps) {
        return ReactSharedInternals.H.useInsertionEffect(create, deps);
      };
      exports.useLayoutEffect = function(create, deps) {
        return ReactSharedInternals.H.useLayoutEffect(create, deps);
      };
      exports.useMemo = function(create, deps) {
        return ReactSharedInternals.H.useMemo(create, deps);
      };
      exports.useOptimistic = function(passthrough, reducer) {
        return ReactSharedInternals.H.useOptimistic(passthrough, reducer);
      };
      exports.useReducer = function(reducer, initialArg, init) {
        return ReactSharedInternals.H.useReducer(reducer, initialArg, init);
      };
      exports.useRef = function(initialValue) {
        return ReactSharedInternals.H.useRef(initialValue);
      };
      exports.useState = function(initialState) {
        return ReactSharedInternals.H.useState(initialState);
      };
      exports.useSyncExternalStore = function(subscribe, getSnapshot, getServerSnapshot) {
        return ReactSharedInternals.H.useSyncExternalStore(
          subscribe,
          getSnapshot,
          getServerSnapshot
        );
      };
      exports.useTransition = function() {
        return ReactSharedInternals.H.useTransition();
      };
      exports.version = "19.2.7";
    }
  });

  // ../../../../../../../../execroot/_main/bazel-out/k8-fastbuild-ST-f481a530da89/bin/external/aemu+/api/web/node_modules/.aspect_rules_js/react@19.2.7/node_modules/react/index.js
  var require_react = __commonJS({
    "../../../../../../../../execroot/_main/bazel-out/k8-fastbuild-ST-f481a530da89/bin/external/aemu+/api/web/node_modules/.aspect_rules_js/react@19.2.7/node_modules/react/index.js"(exports, module) {
      "use strict";
      module.exports = require_react_production();
    }
  });

  // ../../../../../../../../execroot/_main/bazel-out/k8-fastbuild-ST-f481a530da89/bin/external/aemu+/api/web/node_modules/.aspect_rules_js/react@19.2.7/node_modules/react/cjs/react-jsx-runtime.production.js
  var require_react_jsx_runtime_production = __commonJS({
    "../../../../../../../../execroot/_main/bazel-out/k8-fastbuild-ST-f481a530da89/bin/external/aemu+/api/web/node_modules/.aspect_rules_js/react@19.2.7/node_modules/react/cjs/react-jsx-runtime.production.js"(exports) {
      "use strict";
      var REACT_ELEMENT_TYPE = /* @__PURE__ */ Symbol.for("react.transitional.element"), REACT_FRAGMENT_TYPE = /* @__PURE__ */ Symbol.for("react.fragment");
      function jsxProd(type, config, maybeKey) {
        var key = null;
        if (maybeKey !== void 0 && (key = "" + maybeKey), config.key !== void 0 && (key = "" + config.key), "key" in config) {
          maybeKey = {};
          for (var propName in config)
            propName !== "key" && (maybeKey[propName] = config[propName]);
        } else maybeKey = config;
        return config = maybeKey.ref, {
          $$typeof: REACT_ELEMENT_TYPE,
          type,
          key,
          ref: config !== void 0 ? config : null,
          props: maybeKey
        };
      }
      exports.Fragment = REACT_FRAGMENT_TYPE;
      exports.jsx = jsxProd;
      exports.jsxs = jsxProd;
    }
  });

  // ../../../../../../../../execroot/_main/bazel-out/k8-fastbuild-ST-f481a530da89/bin/external/aemu+/api/web/node_modules/.aspect_rules_js/react@19.2.7/node_modules/react/jsx-runtime.js
  var require_jsx_runtime = __commonJS({
    "../../../../../../../../execroot/_main/bazel-out/k8-fastbuild-ST-f481a530da89/bin/external/aemu+/api/web/node_modules/.aspect_rules_js/react@19.2.7/node_modules/react/jsx-runtime.js"(exports, module) {
      "use strict";
      module.exports = require_react_jsx_runtime_production();
    }
  });

  // ../../../../../../../../execroot/_main/bazel-out/k8-fastbuild-ST-f481a530da89/bin/external/aemu+/tools/emu-dev-cli/src/ui/gerrit/index.tsx
  var import_react2 = __toESM(require_react());

  // ../../../../../../../../execroot/_main/bazel-out/k8-fastbuild-ST-f481a530da89/bin/external/aemu+/tools/emu-dev-cli/src/ui/gerrit/runtime.ts
  var VOID_HTML_TAGS = /* @__PURE__ */ new Set([
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr"
  ]), SKIPPED_REACT_PROPS = /* @__PURE__ */ new Set([
    "children",
    "key",
    "ref",
    "dangerouslySetInnerHTML"
  ]), mountedContainers = /* @__PURE__ */ new WeakSet(), HTML_ESCAPE_RE = /[&<>"]/, JS_QUOTE_ESCAPE_RE = /[\\'\"\n\r]/, UPPERCASE_TEST_RE = /[A-Z]/, UPPERCASE_REPLACE_RE = /[A-Z]/g;
  function escapeHtml(value) {
    if (value == null)
      return "";
    if (typeof value == "number" || typeof value == "boolean")
      return String(value);
    let str = typeof value == "string" ? value : String(value);
    return !str || !HTML_ESCAPE_RE.test(str) ? str : str.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }
  function escapeJsQuote(value) {
    if (value == null)
      return "";
    if (typeof value == "number" || typeof value == "boolean")
      return String(value);
    let str = typeof value == "string" ? value : String(value);
    return !str || !JS_QUOTE_ESCAPE_RE.test(str) ? str : str.replace(/\\/g, "\\\\").replace(/'/g, "\\'").replace(/"/g, "&quot;").replace(/\n/g, "\\n").replace(/\r/g, "\\r");
  }
  var EVENT_ATTR_MAP = {
    htmlOnClick: "onclick",
    htmlOnInput: "oninput",
    htmlOnKeyDown: "onkeydown",
    onClick: "onclick",
    onInput: "oninput",
    onChange: "onchange",
    onKeyDown: "onkeydown",
    onBlur: "onblur",
    onFocus: "onfocus",
    onMouseDown: "onmousedown"
  };
  function serializeStyleObject(styleObj) {
    let out = "";
    for (let k in styleObj) {
      let v = styleObj[k];
      if (v == null || v === "") continue;
      let cssKey = UPPERCASE_TEST_RE.test(k) ? k.replace(UPPERCASE_REPLACE_RE, (m) => `-${m.toLowerCase()}`) : k;
      out += `${cssKey}:${String(v)};`;
    }
    return out;
  }
  function serializeSingleAttribute(tagName, key, rawVal) {
    if (SKIPPED_REACT_PROPS.has(key) || typeof rawVal == "function" || tagName === "textarea" && key === "defaultValue" || rawVal == null)
      return "";
    if (key in EVENT_ATTR_MAP)
      return ` ${EVENT_ATTR_MAP[key]}="${String(rawVal)}"`;
    if (key === "htmlStyle" || key === "style") {
      let cssText = typeof rawVal == "object" ? serializeStyleObject(rawVal) : String(rawVal);
      return cssText ? ` style="${escapeHtml(cssText)}"` : "";
    }
    return key === "disabled" || key === "checked" ? rawVal ? ` ${key}` : "" : rawVal === !1 ? "" : ` ${key === "className" ? "class" : key === "tabIndex" ? "tabindex" : key === "colSpan" ? "colspan" : key === "defaultValue" ? "value" : key}="${escapeHtml(rawVal)}"`;
  }
  function serializeElementAttributes(tagName, props) {
    let out = "";
    for (let key in props) {
      let serialized = serializeSingleAttribute(tagName, key, props[key]);
      serialized && (out += serialized);
    }
    return out;
  }
  function extractDangerousInnerHtml(props) {
    let raw = props.dangerouslySetInnerHTML;
    return raw && typeof raw.__html == "string" ? raw.__html : null;
  }
  function renderIntrinsicElement(tagName, props) {
    let attrs = serializeElementAttributes(tagName, props);
    if (VOID_HTML_TAGS.has(tagName))
      return `<${tagName}${attrs}/>`;
    let rawHtml = extractDangerousInnerHtml(props), inner;
    return rawHtml !== null ? inner = rawHtml : tagName === "textarea" && props.children === void 0 && props.defaultValue !== void 0 ? inner = escapeHtml(props.defaultValue) : inner = renderReactToHtml(props.children), `<${tagName}${attrs}>${inner}</${tagName}>`;
  }
  function renderReactArray(nodes) {
    let out = "";
    for (let i = 0; i < nodes.length; i++)
      out += renderReactToHtml(nodes[i]);
    return out;
  }
  function renderReactToHtml(node) {
    if (node == null || typeof node == "boolean")
      return "";
    if (typeof node == "number")
      return String(node);
    if (typeof node == "string")
      return escapeHtml(node);
    if (Array.isArray(node))
      return renderReactArray(node);
    if (typeof node == "object" && "props" in node) {
      let element = node;
      if (typeof element.type == "function") {
        let fnComponent = element.type;
        return renderReactToHtml(fnComponent(element.props));
      }
      return typeof element.type == "string" ? renderIntrinsicElement(element.type, element.props || {}) : renderReactToHtml(element.props?.children ?? null);
    }
    return "";
  }
  function mountReactIsland(container, element) {
    container && (mountedContainers.add(container), container.innerHTML = renderReactToHtml(element));
  }
  function unmountReactIsland(container) {
    container && mountedContainers.has(container) && (container.innerHTML = "", mountedContainers.delete(container));
  }

  // ../../../../../../../../execroot/_main/bazel-out/k8-fastbuild-ST-f481a530da89/bin/external/aemu+/tools/emu-dev-cli/src/ui/gerrit/components/AiSwarmAndSubmitReqs.tsx
  var import_jsx_runtime = __toESM(require_jsx_runtime());
  function AiAnalysisStatusBanner(props) {
    let { num, patchSet = 1, status, errorMsg } = props;
    return status === "thinking" ? /* @__PURE__ */ (0, import_jsx_runtime.jsx)("div", { id: `aiAnalysisPanel-${num}`, className: "ai-intangibles-panel", "data-ai-cl": String(num), children: /* @__PURE__ */ (0, import_jsx_runtime.jsxs)("div", { className: "ai-intangibles-head", children: [
      /* @__PURE__ */ (0, import_jsx_runtime.jsxs)("div", { className: "ai-intangibles-head-left", children: [
        /* @__PURE__ */ (0, import_jsx_runtime.jsx)("span", { className: "btn-spinner" }),
        /* @__PURE__ */ (0, import_jsx_runtime.jsx)("span", { className: "ai-intangibles-title", children: `Problem & Alternatives \xB7 Analyzing ag/${num} (PS${patchSet})\u2026` }),
        /* @__PURE__ */ (0, import_jsx_runtime.jsx)("span", { style: { fontSize: "11.5px", color: "var(--g-muted)" }, children: "Evaluating underlying problem, chosen approach, tradeoffs & codebase alternatives" })
      ] }),
      /* @__PURE__ */ (0, import_jsx_runtime.jsx)("div", { className: "ai-intangibles-actions", children: /* @__PURE__ */ (0, import_jsx_runtime.jsx)(
        "button",
        {
          type: "button",
          className: "ai-intangibles-btn",
          onClick: `cancelClAiAnalysis(${num})`,
          children: "\u2715 Cancel"
        }
      ) })
    ] }) }) : /* @__PURE__ */ (0, import_jsx_runtime.jsx)("div", { id: `aiAnalysisPanel-${num}`, className: "ai-intangibles-panel", "data-ai-cl": String(num), children: /* @__PURE__ */ (0, import_jsx_runtime.jsxs)("div", { className: "ai-intangibles-head", children: [
      /* @__PURE__ */ (0, import_jsx_runtime.jsxs)("div", { className: "ai-intangibles-head-left", children: [
        /* @__PURE__ */ (0, import_jsx_runtime.jsx)("span", { className: "ai-intangibles-title", style: { color: "#fca5a5" }, children: `\u26A0 Problem & Alternatives Unavailable (ag/${num})` }),
        /* @__PURE__ */ (0, import_jsx_runtime.jsx)("span", { style: { fontSize: "11.5px", color: "var(--g-muted)" }, children: errorMsg || "Unknown error" })
      ] }),
      /* @__PURE__ */ (0, import_jsx_runtime.jsxs)("div", { className: "ai-intangibles-actions", children: [
        /* @__PURE__ */ (0, import_jsx_runtime.jsx)(
          "button",
          {
            type: "button",
            className: "ai-intangibles-btn",
            onClick: `analyzeClIntangibles(${num}, true)`,
            children: "\u21BB Retry"
          }
        ),
        /* @__PURE__ */ (0, import_jsx_runtime.jsx)(
          "button",
          {
            type: "button",
            className: "ai-intangibles-btn",
            onClick: `hideAiAnalysisForCl(${num})`,
            children: "\u2715"
          }
        )
      ] })
    ] }) });
  }
  function AiAlternativeCard(props) {
    let { num, alt, howItWorksHtml, tradeoffHtml } = props, rawAltId = String(alt.id || "alt-1"), vTone = String(alt.verdict_tone || "focus"), verdictIcon = String(alt.verdict_icon || "\u2696\uFE0F"), verdictLabel = String(alt.verdict_label || "Alternative");
    return /* @__PURE__ */ (0, import_jsx_runtime.jsxs)(
      "div",
      {
        id: `aiAltCard-${num}-${rawAltId}`,
        className: `ai-alt-card tone-${vTone}`,
        "data-cl": String(num),
        "data-alt-id": rawAltId,
        children: [
          /* @__PURE__ */ (0, import_jsx_runtime.jsxs)("div", { className: "ai-alt-head", children: [
            /* @__PURE__ */ (0, import_jsx_runtime.jsxs)("div", { style: { display: "flex", alignItems: "center", gap: "7px", flexWrap: "wrap" }, children: [
              /* @__PURE__ */ (0, import_jsx_runtime.jsx)("span", { className: "ai-alt-verdict", children: `${verdictIcon} ${verdictLabel}` }),
              /* @__PURE__ */ (0, import_jsx_runtime.jsx)("strong", { style: { color: "#f1f5f9" }, children: alt.title || "" })
            ] }),
            /* @__PURE__ */ (0, import_jsx_runtime.jsxs)("div", { className: "ai-intangibles-actions", children: [
              /* @__PURE__ */ (0, import_jsx_runtime.jsx)(
                "button",
                {
                  type: "button",
                  className: "ai-intangibles-btn",
                  onClick: `openAiAlternativeInComposer(${num}, '${rawAltId}', 'ask')`,
                  title: "Ask Agent to check how feasible this alternative is in the repo",
                  children: "\u{1F4AC} Explore with Agent"
                }
              ),
              /* @__PURE__ */ (0, import_jsx_runtime.jsx)(
                "button",
                {
                  type: "button",
                  className: "ai-intangibles-btn",
                  onClick: `openAiAlternativeInComposer(${num}, '${rawAltId}', 'comment')`,
                  title: "Draft a Gerrit question to the author about this alternative",
                  children: "\u270D\uFE0F Ask Author"
                }
              )
            ] })
          ] }),
          /* @__PURE__ */ (0, import_jsx_runtime.jsx)(
            "div",
            {
              style: { color: "#e2e8f0" },
              dangerouslySetInnerHTML: { __html: howItWorksHtml }
            }
          ),
          tradeoffHtml ? /* @__PURE__ */ (0, import_jsx_runtime.jsx)(
            "div",
            {
              className: "ai-alt-tradeoff",
              dangerouslySetInnerHTML: { __html: `<strong>Tradeoff:</strong> ${tradeoffHtml}` }
            }
          ) : null
        ]
      }
    );
  }
  function CommitAiAnalysisPanel(props) {
    let {
      num,
      isCollapsed,
      scopeTone = "focus",
      scopeIcon = "\u{1F9ED}",
      scopeLabel = "Design Assessment",
      analyzedByLdap = "",
      shared = !1,
      bottomLineHtml = "",
      underlyingProblemHtml = "",
      chosenSolutionHtml = "",
      tradeoffsHtmlList = [],
      alternativesCount = 0,
      alternativesCardsHtml = ""
    } = props;
    return /* @__PURE__ */ (0, import_jsx_runtime.jsxs)(
      "div",
      {
        id: `aiAnalysisPanel-${num}`,
        className: `ai-intangibles-panel${isCollapsed ? " collapsed" : ""}`,
        "data-ai-cl": String(num),
        children: [
          /* @__PURE__ */ (0, import_jsx_runtime.jsxs)("div", { className: "ai-intangibles-head", children: [
            /* @__PURE__ */ (0, import_jsx_runtime.jsxs)("div", { className: "ai-intangibles-head-left", children: [
              /* @__PURE__ */ (0, import_jsx_runtime.jsx)("span", { className: "ai-intangibles-title", children: `\u{1F9E0} Problem & Alternatives \xB7 ag/${num}` }),
              /* @__PURE__ */ (0, import_jsx_runtime.jsx)("span", { className: `ai-scope-badge tone-${scopeTone}`, children: `${scopeIcon} ${scopeLabel}` }),
              analyzedByLdap || shared ? /* @__PURE__ */ (0, import_jsx_runtime.jsx)(
                "span",
                {
                  className: "qa-group-badge",
                  title: `Shared team AI analysis${analyzedByLdap ? ` \xB7 generated by @${analyzedByLdap}` : ""}`,
                  children: `\u{1F465} Shared${analyzedByLdap ? ` \xB7 @${analyzedByLdap}` : ""}`
                }
              ) : null
            ] }),
            /* @__PURE__ */ (0, import_jsx_runtime.jsxs)("div", { className: "ai-intangibles-actions", children: [
              /* @__PURE__ */ (0, import_jsx_runtime.jsx)(
                "button",
                {
                  type: "button",
                  className: "ai-intangibles-btn",
                  onClick: `analyzeClIntangibles(${num}, true)`,
                  title: `Re-run Problem & Alternatives analysis on ag/${num}`,
                  children: "\u21BB Re-run"
                }
              ),
              /* @__PURE__ */ (0, import_jsx_runtime.jsx)(
                "button",
                {
                  type: "button",
                  className: "ai-intangibles-btn ai-collapse-btn",
                  onClick: `toggleAiAnalysisCollapse(${num})`,
                  children: isCollapsed ? "Expand \u25BE" : "Collapse \u25B4"
                }
              ),
              /* @__PURE__ */ (0, import_jsx_runtime.jsx)(
                "button",
                {
                  type: "button",
                  className: "ai-intangibles-btn",
                  onClick: `hideAiAnalysisForCl(${num})`,
                  title: "Hide Problem & Alternatives panel",
                  children: "\u2715"
                }
              )
            ] })
          ] }),
          /* @__PURE__ */ (0, import_jsx_runtime.jsxs)("div", { className: "ai-intangibles-body", children: [
            bottomLineHtml ? /* @__PURE__ */ (0, import_jsx_runtime.jsx)("div", { className: "ai-bottom-line", dangerouslySetInnerHTML: { __html: bottomLineHtml } }) : null,
            /* @__PURE__ */ (0, import_jsx_runtime.jsxs)("div", { className: "ai-intangibles-grid", children: [
              /* @__PURE__ */ (0, import_jsx_runtime.jsxs)("div", { className: "ai-intangibles-box", children: [
                /* @__PURE__ */ (0, import_jsx_runtime.jsx)("div", { className: "ai-intangibles-box-label", children: "Underlying Problem" }),
                /* @__PURE__ */ (0, import_jsx_runtime.jsx)("div", { dangerouslySetInnerHTML: { __html: underlyingProblemHtml } })
              ] }),
              /* @__PURE__ */ (0, import_jsx_runtime.jsxs)("div", { className: "ai-intangibles-box", children: [
                /* @__PURE__ */ (0, import_jsx_runtime.jsx)("div", { className: "ai-intangibles-box-label", children: "Chosen Solution & Tradeoffs" }),
                /* @__PURE__ */ (0, import_jsx_runtime.jsx)("div", { dangerouslySetInnerHTML: { __html: chosenSolutionHtml } }),
                tradeoffsHtmlList.length > 0 ? /* @__PURE__ */ (0, import_jsx_runtime.jsx)("ul", { className: "ai-tradeoff-list", children: tradeoffsHtmlList.map((itemHtml, idx) => /* @__PURE__ */ (0, import_jsx_runtime.jsx)("li", { dangerouslySetInnerHTML: { __html: itemHtml } }, idx)) }) : null
              ] })
            ] }),
            alternativesCount > 0 ? /* @__PURE__ */ (0, import_jsx_runtime.jsxs)("div", { children: [
              /* @__PURE__ */ (0, import_jsx_runtime.jsx)("div", { className: "ai-alt-section-title", children: `Alternative Solutions (${alternativesCount})` }),
              /* @__PURE__ */ (0, import_jsx_runtime.jsx)(
                "div",
                {
                  className: "ai-alt-list",
                  dangerouslySetInnerHTML: { __html: alternativesCardsHtml }
                }
              )
            ] }) : null
          ] })
        ]
      }
    );
  }
  function ClSwarmBadge(props) {
    let { sw, elapsedSec = 0 } = props;
    if (!sw) return null;
    if (sw.state === "running") {
      let done = Number(sw.completed_specialists || 0), total = Number(sw.total_specialists || 8);
      return /* @__PURE__ */ (0, import_jsx_runtime.jsxs)("span", { className: "slot slot-swarm-running", children: [
        /* @__PURE__ */ (0, import_jsx_runtime.jsx)("span", { className: "swarm-spinner" }),
        ` Review Swarm \xB7 ${done}/${total} (${elapsedSec}s)`
      ] });
    }
    if (sw.state === "completed") {
      let findings = Number(sw.total_findings || (sw.findings ? sw.findings.length : 0)), wall = sw.wall_clock_sec ? `${Number(sw.wall_clock_sec).toFixed(1)}s` : "done";
      return findings === 0 ? /* @__PURE__ */ (0, import_jsx_runtime.jsx)("span", { className: "slot slot-swarm-clean", children: `\u2713 Swarm Clean (${wall})` }) : /* @__PURE__ */ (0, import_jsx_runtime.jsx)("span", { className: "slot slot-swarm-findings", children: `\u2605 Swarm: ${findings} finding${findings === 1 ? "" : "s"} (${wall})` });
    }
    return null;
  }
  function SpecialistChips(props) {
    let { agents, isRunning = !1, defaultSpecialists = [] } = props, list = Array.isArray(agents) && agents.length > 0 ? agents : defaultSpecialists;
    return /* @__PURE__ */ (0, import_jsx_runtime.jsx)(import_jsx_runtime.Fragment, { children: list.map((a, idx) => {
      let roleLabel = String(a.role || a.specialist || "Specialist").replace(/\s+Auditor$/i, "");
      if ((a.status || (isRunning ? "running" : "done")) === "done") {
        let fCnt = Number(a.finding_count || 0), dur = a.duration_sec != null ? ` \xB7 ${Number(a.duration_sec).toFixed(1)}s` : "";
        return fCnt > 0 ? /* @__PURE__ */ (0, import_jsx_runtime.jsx)("span", { className: "swarm-spec-chip done-finding", children: `\u2605 ${roleLabel} (${fCnt}${dur})` }, idx) : /* @__PURE__ */ (0, import_jsx_runtime.jsx)("span", { className: "swarm-spec-chip done-clean", children: `\u2713 ${roleLabel}${dur}` }, idx);
      }
      return /* @__PURE__ */ (0, import_jsx_runtime.jsxs)("span", { className: "swarm-spec-chip running", children: [
        /* @__PURE__ */ (0, import_jsx_runtime.jsx)("span", { className: "swarm-spinner", style: { width: "9px", height: "9px", borderWidth: "1.5px" } }),
        ` ${roleLabel}`
      ] }, idx);
    }) });
  }
  function SwarmFindingsList(props) {
    let { clNum, findings } = props;
    return !Array.isArray(findings) || findings.length === 0 ? null : /* @__PURE__ */ (0, import_jsx_runtime.jsx)(import_jsx_runtime.Fragment, { children: findings.map((f, idx) => {
      let loc = `${f.file || "/COMMIT_MSG"}:${f.line || 1}`, cat = f.category || "review", sev = f.severity || "blocking";
      return /* @__PURE__ */ (0, import_jsx_runtime.jsxs)("div", { className: "swarm-finding", style: { margin: "6px 12px 10px 12px" }, children: [
        /* @__PURE__ */ (0, import_jsx_runtime.jsxs)("div", { className: "swarm-finding-head", children: [
          /* @__PURE__ */ (0, import_jsx_runtime.jsxs)("span", { children: [
            "\u2605 Swarm Finding \xB7 ",
            f.locHtml ? /* @__PURE__ */ (0, import_jsx_runtime.jsx)("code", { dangerouslySetInnerHTML: { __html: f.locHtml } }) : /* @__PURE__ */ (0, import_jsx_runtime.jsx)("code", { children: loc }),
            ` [${cat} \xB7 ${sev}]`
          ] }),
          /* @__PURE__ */ (0, import_jsx_runtime.jsx)(
            "button",
            {
              className: "nudge-btn",
              onClick: `postSwarmFindingsToGerrit(${clNum}, this)`,
              children: "Post to Gerrit (notify: OWNER)"
            }
          )
        ] }),
        /* @__PURE__ */ (0, import_jsx_runtime.jsx)("div", { style: { color: "var(--g-text)", fontSize: "12px" }, children: f.message || "" }),
        f.suggested_fix ? /* @__PURE__ */ (0, import_jsx_runtime.jsx)(
          "div",
          {
            style: {
              marginTop: "6px",
              padding: "5px 8px",
              background: "#18191d",
              border: "1px solid var(--g-border)",
              borderRadius: "4px",
              fontFamily: "var(--g-mono)",
              fontSize: "11px",
              color: "#b7e3c4"
            },
            children: `Suggested fix: ${f.suggested_fix}`
          }
        ) : null
      ] }, idx);
    }) });
  }
  function CommitSwarmPanel(props) {
    let { clNum, sw, elapsedSec = 0, defaultSpecialists = [] } = props;
    if (!sw) return null;
    if (sw.state === "running") {
      let done = Number(sw.completed_specialists || 0), total = Math.max(1, Number(sw.total_specialists || 8)), pct = Math.min(96, Math.max(10, Math.round(done / total * 88) + 8)), phaseLabel = sw.phase === "auditing" ? `Auditing Diff in Parallel \xB7 ${done}/${total} Specialists Done \xB7 ${elapsedSec}s` : `Preparing Bundle & Launching 8 Specialists \xB7 ${done}/${total} \xB7 ${elapsedSec}s`;
      return /* @__PURE__ */ (0, import_jsx_runtime.jsxs)("div", { className: "swarm-live-panel", children: [
        /* @__PURE__ */ (0, import_jsx_runtime.jsx)("div", { className: "swarm-progress-track", children: /* @__PURE__ */ (0, import_jsx_runtime.jsx)("div", { className: "swarm-progress-bar", style: { width: `${pct}%` } }) }),
        /* @__PURE__ */ (0, import_jsx_runtime.jsxs)("div", { className: "swarm-panel-header", children: [
          /* @__PURE__ */ (0, import_jsx_runtime.jsxs)("div", { style: { display: "flex", alignItems: "center", gap: "8px" }, children: [
            /* @__PURE__ */ (0, import_jsx_runtime.jsx)("span", { className: "swarm-spinner" }),
            /* @__PURE__ */ (0, import_jsx_runtime.jsx)("strong", { style: { color: "#d2c4f0" }, children: `8-Specialist Review Swarm Running on ag/${clNum}` })
          ] }),
          /* @__PURE__ */ (0, import_jsx_runtime.jsx)("span", { style: { fontSize: "11.5px", fontWeight: 600, color: "var(--g-muted)" }, children: phaseLabel })
        ] }),
        /* @__PURE__ */ (0, import_jsx_runtime.jsx)("div", { className: "swarm-specialist-grid", children: /* @__PURE__ */ (0, import_jsx_runtime.jsx)(
          SpecialistChips,
          {
            agents: sw.agents,
            isRunning: !0,
            defaultSpecialists
          }
        ) })
      ] });
    }
    if (sw.state === "completed") {
      let findings = Array.isArray(sw.findings) ? sw.findings : [], fCnt = Number(sw.total_findings != null ? sw.total_findings : findings.length), wall = sw.wall_clock_sec ? `${Number(sw.wall_clock_sec).toFixed(1)}s` : "", speedup = sw.parallel_speedup ? ` \xB7 ${Number(sw.parallel_speedup).toFixed(2)}x parallel speedup` : "", cleanCls = fCnt === 0 ? "completed-clean" : "", headline = fCnt === 0 ? "\u2713 8-Specialist Review Swarm Clean (0 findings)" : `\u2605 8-Specialist Review Swarm Complete \xB7 ${fCnt} Verified Finding${fCnt === 1 ? "" : "s"}`, headColor = fCnt === 0 ? "#96d9ab" : "#d2c4f0";
      return /* @__PURE__ */ (0, import_jsx_runtime.jsxs)("div", { className: `swarm-live-panel ${cleanCls}`, children: [
        /* @__PURE__ */ (0, import_jsx_runtime.jsxs)("div", { className: "swarm-panel-header", children: [
          /* @__PURE__ */ (0, import_jsx_runtime.jsxs)("div", { style: { display: "flex", alignItems: "center", gap: "8px", flexWrap: "wrap" }, children: [
            /* @__PURE__ */ (0, import_jsx_runtime.jsx)("strong", { style: { color: headColor }, children: headline }),
            /* @__PURE__ */ (0, import_jsx_runtime.jsx)("span", { style: { fontSize: "11.5px", color: "var(--g-muted)" }, children: wall + speedup })
          ] }),
          /* @__PURE__ */ (0, import_jsx_runtime.jsxs)("div", { style: { display: "flex", alignItems: "center", gap: "6px" }, children: [
            fCnt > 0 ? /* @__PURE__ */ (0, import_jsx_runtime.jsx)(
              "button",
              {
                className: "nudge-btn",
                onClick: `postSwarmFindingsToGerrit(${clNum}, this)`,
                children: `Post Finding${fCnt === 1 ? "" : "s"} to Gerrit (notify: OWNER)`
              }
            ) : null,
            /* @__PURE__ */ (0, import_jsx_runtime.jsx)(
              "button",
              {
                className: "nudge-btn",
                onClick: `triggerHarnessForCl(${clNum}, 'review-swarm', this)`,
                children: "\u21BB Re-Run Swarm"
              }
            )
          ] })
        ] }),
        /* @__PURE__ */ (0, import_jsx_runtime.jsx)("div", { className: "swarm-specialist-grid", children: /* @__PURE__ */ (0, import_jsx_runtime.jsx)(
          SpecialistChips,
          {
            agents: sw.agents,
            isRunning: !1,
            defaultSpecialists
          }
        ) }),
        /* @__PURE__ */ (0, import_jsx_runtime.jsx)(SwarmFindingsList, { clNum, findings })
      ] });
    }
    return null;
  }
  function VoteBoxChip(props) {
    let { name, val, labelName, avatarBg, initial } = props, v = Number(val || 0), cls = v >= 2 ? "v-pos2" : v === 1 ? "v-pos1" : v === -1 ? "v-neg1" : "v-neg2", fmt = v > 0 ? `+${v}` : String(v);
    return /* @__PURE__ */ (0, import_jsx_runtime.jsxs)("span", { className: `pg-vote-box ${cls}`, title: `${name}: ${labelName} ${fmt}`, children: [
      /* @__PURE__ */ (0, import_jsx_runtime.jsx)("span", { className: "pg-account-avatar", style: { background: avatarBg }, children: initial }),
      /* @__PURE__ */ (0, import_jsx_runtime.jsx)("span", { children: fmt })
    ] });
  }
  function SrAtomsList(props) {
    let { sr } = props, passAtoms = Array.isArray(sr.passing_atoms) ? sr.passing_atoms : [], failAtoms = Array.isArray(sr.failing_atoms) ? sr.failing_atoms : [];
    return passAtoms.length === 0 && failAtoms.length === 0 ? null : /* @__PURE__ */ (0, import_jsx_runtime.jsxs)("ul", { className: "pg-sr-atom-list", children: [
      passAtoms.map((a, idx) => /* @__PURE__ */ (0, import_jsx_runtime.jsxs)("li", { className: "pg-sr-atom pg-sr-atom-pass", children: [
        /* @__PURE__ */ (0, import_jsx_runtime.jsx)("span", { className: "atom-icon", "aria-hidden": "true", children: "\u2713" }),
        " ",
        a
      ] }, `pass-${idx}`)),
      failAtoms.map((a, idx) => /* @__PURE__ */ (0, import_jsx_runtime.jsxs)("li", { className: "pg-sr-atom pg-sr-atom-fail", children: [
        /* @__PURE__ */ (0, import_jsx_runtime.jsx)("span", { className: "atom-icon", "aria-hidden": "true", children: "\u2715" }),
        " ",
        a
      ] }, `fail-${idx}`))
    ] });
  }
  function SrActionBtn(props) {
    let { clNum, sr } = props, name = sr.name || "";
    return name === "Code-Review" || name === "Verified" ? /* @__PURE__ */ (0, import_jsx_runtime.jsx)(
      "button",
      {
        type: "button",
        className: "pg-btn-subtle",
        onClick: "event.stopPropagation(); openReplyDialog({ focusComment: false })",
        title: `Open Reply dialog to vote ${name}`,
        children: `Vote ${name} (r)`
      }
    ) : name === "Presubmit-Verified" ? /* @__PURE__ */ (0, import_jsx_runtime.jsx)(
      "button",
      {
        type: "button",
        className: "pg-btn-subtle",
        onClick: `event.stopPropagation(); selectPgChangeTab(${clNum}, 'checks')`,
        title: "Inspect TreeHugger presubmit checks in Checks tab",
        children: "Inspect Checks Tab"
      }
    ) : /* @__PURE__ */ (0, import_jsx_runtime.jsx)(
      "button",
      {
        type: "button",
        className: "pg-btn-subtle",
        onClick: "event.stopPropagation(); openReplyDialog({ focusComment: false })",
        title: "Open Reply dialog",
        children: "Reply / Vote"
      }
    );
  }
  function SrDetailRow(props) {
    let { clNum, sr } = props, status = String(sr.status || "UNSATISFIED").toUpperCase(), statusCls = "tag-unsatisfied";
    return status === "SATISFIED" || status === "OVERRIDDEN" ? statusCls = "tag-satisfied" : status === "BLOCKED" ? statusCls = "tag-blocked" : status === "NOT_APPLICABLE" && (statusCls = "tag-na"), /* @__PURE__ */ (0, import_jsx_runtime.jsx)("tr", { className: "pg-sr-detail-row", children: /* @__PURE__ */ (0, import_jsx_runtime.jsx)("td", { colSpan: 4, className: "pg-sr-detail-cell", children: /* @__PURE__ */ (0, import_jsx_runtime.jsxs)("div", { className: "pg-sr-detail-box", children: [
      /* @__PURE__ */ (0, import_jsx_runtime.jsxs)("div", { className: "pg-sr-detail-header", children: [
        /* @__PURE__ */ (0, import_jsx_runtime.jsx)("span", { className: `pg-sr-status-tag ${statusCls}`, children: status }),
        sr.description ? /* @__PURE__ */ (0, import_jsx_runtime.jsx)("span", { className: "pg-sr-desc", children: sr.description }) : null
      ] }),
      sr.expression ? /* @__PURE__ */ (0, import_jsx_runtime.jsxs)("div", { className: "pg-sr-expression-block", children: [
        /* @__PURE__ */ (0, import_jsx_runtime.jsx)("span", { className: "pg-sr-expr-label", children: "Expression:" }),
        /* @__PURE__ */ (0, import_jsx_runtime.jsx)("code", { className: "pg-sr-code", children: sr.expression })
      ] }) : null,
      /* @__PURE__ */ (0, import_jsx_runtime.jsx)(SrAtomsList, { sr }),
      /* @__PURE__ */ (0, import_jsx_runtime.jsx)("div", { className: "pg-sr-detail-actions", children: /* @__PURE__ */ (0, import_jsx_runtime.jsx)(SrActionBtn, { clNum, sr }) })
    ] }) }) });
  }
  function CrSubmitRequirementRow(props) {
    let { satisfied, minVote, reqCr2, doneCr2, chipsHtml } = props, rowTitle = reqCr2 >= 2 ? `Requires ${reqCr2} human Code-Review +2 approvals (click to Reply)` : "Requires Code-Review +2 approval (click to Reply)", hintNode = null;
    return minVote > -2 && doneCr2 < reqCr2 && reqCr2 >= 2 ? hintNode = /* @__PURE__ */ (0, import_jsx_runtime.jsx)("span", { className: "pg-sr-hint", children: doneCr2 === 0 ? `Requires ${reqCr2} votes` : `${reqCr2 - doneCr2} more +2 needed` }) : chipsHtml || (hintNode = /* @__PURE__ */ (0, import_jsx_runtime.jsx)("span", { className: "pg-no-votes", children: "No votes" })), /* @__PURE__ */ (0, import_jsx_runtime.jsxs)(
      "tr",
      {
        className: "pg-sr-row",
        onClick: "event.stopPropagation(); openReplyDialog({ focusComment: false })",
        title: rowTitle,
        children: [
          /* @__PURE__ */ (0, import_jsx_runtime.jsx)("td", { className: "pg-sr-icon-cell", children: satisfied ? /* @__PURE__ */ (0, import_jsx_runtime.jsx)("span", { className: "pg-sr-icon sr-ok", title: "Submit requirement satisfied", children: "\u2713" }) : /* @__PURE__ */ (0, import_jsx_runtime.jsx)(
            "span",
            {
              className: "pg-sr-icon sr-unsat",
              title: minVote <= -2 ? "Blocked by Code-Review -2" : `Needs ${reqCr2} human Code-Review +2 approval${reqCr2 === 1 ? "" : "s"}`,
              children: "\u2715"
            }
          ) }),
          /* @__PURE__ */ (0, import_jsx_runtime.jsx)("td", { className: "pg-sr-name", children: "Code-Review" }),
          /* @__PURE__ */ (0, import_jsx_runtime.jsxs)("td", { className: "pg-sr-votes", children: [
            chipsHtml ? /* @__PURE__ */ (0, import_jsx_runtime.jsx)("span", { dangerouslySetInnerHTML: { __html: chipsHtml } }) : null,
            hintNode
          ] })
        ]
      }
    );
  }
  function PvSubmitRequirementRow(props) {
    let { clNum, satisfied, running, tooltip, votesHtml } = props;
    return /* @__PURE__ */ (0, import_jsx_runtime.jsxs)(
      "tr",
      {
        className: "pg-sr-row",
        onClick: `event.stopPropagation(); selectPgChangeTab(${clNum}, 'checks')`,
        title: tooltip || "Click to inspect TreeHugger presubmit runs in Checks tab",
        children: [
          /* @__PURE__ */ (0, import_jsx_runtime.jsx)("td", { className: "pg-sr-icon-cell", children: satisfied ? /* @__PURE__ */ (0, import_jsx_runtime.jsx)("span", { className: "pg-sr-icon sr-ok", title: "Submit requirement satisfied", children: "\u2713" }) : running ? /* @__PURE__ */ (0, import_jsx_runtime.jsx)("span", { className: "pg-sr-icon sr-warn", title: tooltip || "Presubmit running", children: "\u25F7" }) : /* @__PURE__ */ (0, import_jsx_runtime.jsx)("span", { className: "pg-sr-icon sr-unsat", title: tooltip || "Needs Presubmit-Verified +2", children: "\u2715" }) }),
          /* @__PURE__ */ (0, import_jsx_runtime.jsx)("td", { className: "pg-sr-name", children: "Presubmit-Verified" }),
          /* @__PURE__ */ (0, import_jsx_runtime.jsx)("td", { className: "pg-sr-votes", dangerouslySetInnerHTML: { __html: votesHtml } })
        ]
      }
    );
  }
  function TriggerVotesSection(props) {
    let { prVotesHtml, asVotesHtml, extraPills } = props;
    return /* @__PURE__ */ (0, import_jsx_runtime.jsxs)("div", { className: "pg-trigger-votes-section", children: [
      /* @__PURE__ */ (0, import_jsx_runtime.jsx)("h4", { className: "pg-metadata-title", children: "Trigger Votes" }),
      /* @__PURE__ */ (0, import_jsx_runtime.jsxs)("div", { className: "pg-trigger-list", children: [
        /* @__PURE__ */ (0, import_jsx_runtime.jsxs)(
          "button",
          {
            type: "button",
            className: "pg-trigger-pill",
            onClick: "event.stopPropagation(); openReplyDialog({ focusComment: false })",
            title: "Presubmit-Ready trigger vote (click to stage via Reply dialog)",
            children: [
              /* @__PURE__ */ (0, import_jsx_runtime.jsx)("span", { children: "Presubmit-Ready" }),
              /* @__PURE__ */ (0, import_jsx_runtime.jsx)("span", { dangerouslySetInnerHTML: { __html: prVotesHtml } })
            ]
          }
        ),
        /* @__PURE__ */ (0, import_jsx_runtime.jsxs)(
          "button",
          {
            type: "button",
            className: "pg-trigger-pill",
            onClick: "event.stopPropagation(); openReplyDialog({ focusComment: false })",
            title: "Autosubmit trigger vote (click to stage via Reply dialog)",
            children: [
              /* @__PURE__ */ (0, import_jsx_runtime.jsx)("span", { children: "Autosubmit" }),
              /* @__PURE__ */ (0, import_jsx_runtime.jsx)("span", { dangerouslySetInnerHTML: { __html: asVotesHtml } })
            ]
          }
        ),
        extraPills.map((ep, idx) => /* @__PURE__ */ (0, import_jsx_runtime.jsxs)(
          "button",
          {
            type: "button",
            className: "pg-trigger-pill",
            onClick: "event.stopPropagation(); openReplyDialog({ focusComment: false })",
            title: "Click to open Reply dialog",
            children: [
              /* @__PURE__ */ (0, import_jsx_runtime.jsx)("span", { children: ep.label }),
              /* @__PURE__ */ (0, import_jsx_runtime.jsx)("span", { dangerouslySetInnerHTML: { __html: ep.chipsHtml } })
            ]
          },
          idx
        ))
      ] })
    ] });
  }
  function SubmitRequirementsSection(props) {
    let { clNum, satisfiedCount, totalCount, rowsHtml, triggerVotesHtml } = props, badgeCls = totalCount > 0 && satisfiedCount === totalCount ? "sr-badge-ok" : "sr-badge-pending";
    return /* @__PURE__ */ (0, import_jsx_runtime.jsxs)("section", { className: "pg-submit-requirements", children: [
      /* @__PURE__ */ (0, import_jsx_runtime.jsxs)("div", { className: "pg-sr-header", children: [
        /* @__PURE__ */ (0, import_jsx_runtime.jsxs)("h3", { className: "pg-metadata-title", children: [
          "Submit Requirements ",
          /* @__PURE__ */ (0, import_jsx_runtime.jsx)("span", { className: `pg-sr-count-badge ${badgeCls}`, children: `${satisfiedCount}/${totalCount}` })
        ] }),
        /* @__PURE__ */ (0, import_jsx_runtime.jsx)(
          "button",
          {
            type: "button",
            className: "pg-btn-subtle pg-btn-sr-audit",
            onClick: `event.stopPropagation(); openPgSubmitRequirementsAuditDialog(${clNum})`,
            title: "Audit all submit requirements and trigger votes in detail (Shortcut: Shift+Z)",
            children: /* @__PURE__ */ (0, import_jsx_runtime.jsx)("span", { children: "\u{1F4CB} Audit" })
          }
        )
      ] }),
      /* @__PURE__ */ (0, import_jsx_runtime.jsx)("table", { className: "pg-sr-table", children: /* @__PURE__ */ (0, import_jsx_runtime.jsx)("tbody", { dangerouslySetInnerHTML: { __html: rowsHtml } }) }),
      /* @__PURE__ */ (0, import_jsx_runtime.jsx)("div", { dangerouslySetInnerHTML: { __html: triggerVotesHtml } })
    ] });
  }
  function SrAuditCard(props) {
    let {
      clNum,
      sr,
      highlightedExprHtml = "",
      highlightedAppHtml = "",
      highlightedOvrHtml = "",
      crChipsHtml = ""
    } = props, name = sr.name || "Requirement", status = String(sr.status || "UNSATISFIED").toUpperCase(), cardCls = "is-unsatisfied", icon = "\u2715", iconCls = "icon-unsatisfied", statusLabel = status;
    status === "SATISFIED" ? (cardCls = "is-satisfied", icon = "\u2713", iconCls = "icon-satisfied") : status === "OVERRIDDEN" ? (cardCls = "is-overridden", icon = "\u21B7", iconCls = "icon-overridden") : status === "BLOCKED" ? (cardCls = "is-blocked", icon = "\u2715", iconCls = "icon-blocked") : status === "NOT_APPLICABLE" ? (cardCls = "is-na", icon = "\u2014", iconCls = "icon-na") : status === "RUNNING" && (cardCls = "is-running", icon = "\u25F7", iconCls = "icon-unsatisfied");
    let passAtoms = Array.isArray(sr.passing_atoms) ? sr.passing_atoms : [], failAtoms = Array.isArray(sr.failing_atoms) ? sr.failing_atoms : [];
    return /* @__PURE__ */ (0, import_jsx_runtime.jsxs)(
      "div",
      {
        className: `pg-sr-audit-card ${cardCls}`,
        "data-sr-name": name,
        "data-sr-status": status,
        children: [
          /* @__PURE__ */ (0, import_jsx_runtime.jsxs)("div", { className: "pg-sr-card-head", children: [
            /* @__PURE__ */ (0, import_jsx_runtime.jsxs)("div", { className: "pg-sr-card-head-left", children: [
              /* @__PURE__ */ (0, import_jsx_runtime.jsx)("span", { className: `pg-sr-card-icon ${iconCls}`, "aria-hidden": "true", children: icon }),
              /* @__PURE__ */ (0, import_jsx_runtime.jsx)("span", { className: "pg-sr-card-name", children: name }),
              /* @__PURE__ */ (0, import_jsx_runtime.jsx)("span", { className: `pg-sr-status-tag tag-${status.toLowerCase()}`, children: statusLabel })
            ] }),
            /* @__PURE__ */ (0, import_jsx_runtime.jsx)("span", { className: "pg-meta-pill", style: { fontSize: "11px" }, children: status === "NOT_APPLICABLE" ? "Optional / N/A" : "Blocking" })
          ] }),
          sr.description ? /* @__PURE__ */ (0, import_jsx_runtime.jsx)("div", { className: "pg-sr-card-desc", children: sr.description }) : null,
          sr.applicability_expression ? /* @__PURE__ */ (0, import_jsx_runtime.jsxs)("div", { className: "pg-sr-card-expr-row", children: [
            /* @__PURE__ */ (0, import_jsx_runtime.jsx)("span", { className: "pg-sr-card-expr-label", children: "Applicable if:" }),
            /* @__PURE__ */ (0, import_jsx_runtime.jsx)(
              "code",
              {
                className: "pg-sr-card-code",
                dangerouslySetInnerHTML: { __html: highlightedAppHtml }
              }
            )
          ] }) : null,
          sr.expression ? /* @__PURE__ */ (0, import_jsx_runtime.jsxs)("div", { className: "pg-sr-card-expr-row", children: [
            /* @__PURE__ */ (0, import_jsx_runtime.jsx)("span", { className: "pg-sr-card-expr-label", children: "Submittable if:" }),
            /* @__PURE__ */ (0, import_jsx_runtime.jsx)(
              "code",
              {
                className: "pg-sr-card-code",
                dangerouslySetInnerHTML: { __html: highlightedExprHtml }
              }
            )
          ] }) : null,
          sr.override_expression ? /* @__PURE__ */ (0, import_jsx_runtime.jsxs)("div", { className: "pg-sr-card-expr-row", children: [
            /* @__PURE__ */ (0, import_jsx_runtime.jsx)("span", { className: "pg-sr-card-expr-label", children: "Overridden if:" }),
            /* @__PURE__ */ (0, import_jsx_runtime.jsx)(
              "code",
              {
                className: "pg-sr-card-code",
                dangerouslySetInnerHTML: { __html: highlightedOvrHtml }
              }
            )
          ] }) : null,
          passAtoms.length > 0 || failAtoms.length > 0 ? /* @__PURE__ */ (0, import_jsx_runtime.jsxs)("ul", { className: "pg-sr-atoms-list", children: [
            passAtoms.map((a, idx) => /* @__PURE__ */ (0, import_jsx_runtime.jsxs)("li", { className: "pg-sr-atom-item is-pass", children: [
              /* @__PURE__ */ (0, import_jsx_runtime.jsx)("span", { "aria-hidden": "true", children: "\u2713" }),
              " ",
              /* @__PURE__ */ (0, import_jsx_runtime.jsx)("span", { children: a })
            ] }, `p-${idx}`)),
            failAtoms.map((a, idx) => /* @__PURE__ */ (0, import_jsx_runtime.jsxs)("li", { className: "pg-sr-atom-item is-fail", children: [
              /* @__PURE__ */ (0, import_jsx_runtime.jsx)("span", { "aria-hidden": "true", children: "\u2715" }),
              " ",
              /* @__PURE__ */ (0, import_jsx_runtime.jsx)("span", { children: a })
            ] }, `f-${idx}`))
          ] }) : null,
          name === "Code-Review" && crChipsHtml ? /* @__PURE__ */ (0, import_jsx_runtime.jsxs)("div", { className: "pg-sr-card-voters", children: [
            /* @__PURE__ */ (0, import_jsx_runtime.jsx)("span", { className: "pg-sr-card-expr-label", children: "Current votes:" }),
            " ",
            /* @__PURE__ */ (0, import_jsx_runtime.jsx)("span", { dangerouslySetInnerHTML: { __html: crChipsHtml } })
          ] }) : null,
          /* @__PURE__ */ (0, import_jsx_runtime.jsx)("div", { className: "pg-sr-card-actions", children: /* @__PURE__ */ (0, import_jsx_runtime.jsx)(SrActionBtn, { clNum, sr }) })
        ]
      }
    );
  }
  function renderAiAnalysisStatusBannerHtml(num, patchSet, status, errorMsg) {
    return renderReactToHtml(
      /* @__PURE__ */ (0, import_jsx_runtime.jsx)(
        AiAnalysisStatusBanner,
        {
          num: Number(num),
          patchSet: Number(patchSet || 1),
          status,
          errorMsg
        }
      )
    );
  }
  function renderAiAlternativeCardHtml(num, alt, howItWorksHtml, tradeoffHtml = "", attachedItemsHtml = "") {
    return `${renderReactToHtml(
      /* @__PURE__ */ (0, import_jsx_runtime.jsx)(
        AiAlternativeCard,
        {
          num: Number(num),
          alt: alt || {},
          howItWorksHtml: howItWorksHtml || "",
          tradeoffHtml: tradeoffHtml || ""
        }
      )
    )}${attachedItemsHtml || ""}`;
  }
  function renderCommitAiAnalysisPanelHtml(props) {
    return !props || !props.num ? "" : renderReactToHtml(/* @__PURE__ */ (0, import_jsx_runtime.jsx)(CommitAiAnalysisPanel, { ...props }));
  }
  function renderClSwarmBadgeHtml(sw, elapsedSec = 0) {
    return sw ? renderReactToHtml(/* @__PURE__ */ (0, import_jsx_runtime.jsx)(ClSwarmBadge, { sw, elapsedSec })) : "";
  }
  function renderSpecialistChipsHtml(agents, isRunning = !1, defaultSpecialists = []) {
    return renderReactToHtml(
      /* @__PURE__ */ (0, import_jsx_runtime.jsx)(
        SpecialistChips,
        {
          agents,
          isRunning: !!isRunning,
          defaultSpecialists
        }
      )
    );
  }
  function renderSwarmFindingsListHtml(clNum, findings) {
    return !Array.isArray(findings) || findings.length === 0 ? "" : renderReactToHtml(/* @__PURE__ */ (0, import_jsx_runtime.jsx)(SwarmFindingsList, { clNum: Number(clNum), findings }));
  }
  function renderCommitSwarmPanelHtml(clNum, sw, elapsedSec = 0, defaultSpecialists = []) {
    return sw ? renderReactToHtml(
      /* @__PURE__ */ (0, import_jsx_runtime.jsx)(
        CommitSwarmPanel,
        {
          clNum: Number(clNum),
          sw,
          elapsedSec,
          defaultSpecialists
        }
      )
    ) : "";
  }
  function renderPgVoteBoxChipHtml(whoObj, val, labelName, avatarBg, initial) {
    let name = whoObj && (whoObj.name || whoObj.short || whoObj.email) || "user";
    return renderReactToHtml(
      /* @__PURE__ */ (0, import_jsx_runtime.jsx)(
        VoteBoxChip,
        {
          name,
          val: Number(val || 0),
          labelName: labelName || "",
          avatarBg: avatarBg || "#5f6368",
          initial: initial || "U"
        }
      )
    );
  }
  function renderPgSrAtomsHtml(sr) {
    return sr ? renderReactToHtml(/* @__PURE__ */ (0, import_jsx_runtime.jsx)(SrAtomsList, { sr })) : "";
  }
  function renderPgSrActionBtnHtml(c, sr) {
    let clNum = Number(c && c.number || 0);
    return renderReactToHtml(/* @__PURE__ */ (0, import_jsx_runtime.jsx)(SrActionBtn, { clNum, sr: sr || {} }));
  }
  function renderPgSrDetailRowHtml(c, sr) {
    let clNum = Number(c && c.number || 0);
    return renderReactToHtml(/* @__PURE__ */ (0, import_jsx_runtime.jsx)(SrDetailRow, { clNum, sr: sr || {} }));
  }
  function renderPgCrSubmitRequirementRowHtml(props) {
    return renderReactToHtml(/* @__PURE__ */ (0, import_jsx_runtime.jsx)(CrSubmitRequirementRow, { ...props }));
  }
  function renderPgPvSubmitRequirementRowHtml(props) {
    return renderReactToHtml(/* @__PURE__ */ (0, import_jsx_runtime.jsx)(PvSubmitRequirementRow, { ...props }));
  }
  function renderPgTriggerVotesSectionHtml(props) {
    return renderReactToHtml(/* @__PURE__ */ (0, import_jsx_runtime.jsx)(TriggerVotesSection, { ...props }));
  }
  function renderPgSubmitRequirementsSectionHtml(props) {
    return renderReactToHtml(/* @__PURE__ */ (0, import_jsx_runtime.jsx)(SubmitRequirementsSection, { ...props }));
  }
  function renderPgSrAuditCardHtml(props) {
    return renderReactToHtml(/* @__PURE__ */ (0, import_jsx_runtime.jsx)(SrAuditCard, { ...props }));
  }

  // ../../../../../../../../execroot/_main/bazel-out/k8-fastbuild-ST-f481a530da89/bin/external/aemu+/tools/emu-dev-cli/src/ui/gerrit/state/store.ts
  var import_react = __toESM(require_react());

  // ../../../../../../../../execroot/_main/bazel-out/k8-fastbuild-ST-f481a530da89/bin/external/aemu+/tools/emu-dev-cli/src/ui/gerrit/constants.ts
  var CHANGE_STATUS_MERGED = "MERGED", CHANGE_STATUS_ABANDONED = "ABANDONED";
  var REFS_HEADS_PREFIX = "refs/heads/", DEFAULT_BRANCH_NAME = "main", DEFAULT_BASE_LABEL = "base", DEFAULT_TARGET_BRANCH_LABEL = "target branch", PATCHSET_BASE_TOKEN = "base";
  var RELATION_HINT_CONTINUOUS_CHAIN = "Click commit to jump \xB7 Shift+S single CL \xB7 Alt+I stack interdiff \xB7 [/] step PS \xB7 x batch select", RELATION_HINT_SINGLE_CHAIN = "Shift+[/] step chain \xB7 Shift+S whole stack \xB7 Alt+I stack interdiff \xB7 [/] step PS \xB7 b toggle interdiff \xB7 x batch select", RELATION_HINT_SUBMITTED = "Click change to view \xB7 \u{1F50D} Filter isolates submitted together changes on dashboard", RELATION_HINT_CHERRYPICKS = "Cherry-picks sharing Change-Id across branches \xB7 \u{1F352} Cherry-pick creates copy on another branch", RELATION_HINT_TOPIC = "Click change to view \xB7 \u{1F50D} Filter isolates topic on dashboard", RELATION_HINT_CYCLE_TABS_SUFFIX = " \xB7 Press <kbd>Alt+R</kbd> to cycle tabs";

  // ../../../../../../../../execroot/_main/bazel-out/k8-fastbuild-ST-f481a530da89/bin/external/aemu+/tools/emu-dev-cli/src/ui/gerrit/state/store.ts
  var GerritUIStore = class {
    revision = 0;
    listeners = /* @__PURE__ */ new Set();
    cachedSnapshot = {
      revision: 0,
      selectedCl: null,
      selectedStackId: null,
      continuousStackDiff: !1
    };
    subscribe = (listener) => (this.listeners.add(listener), () => {
      this.listeners.delete(listener);
    });
    getSnapshot = () => this.cachedSnapshot;
    notify() {
      this.revision += 1;
      let hostState = getHostStateObject();
      this.cachedSnapshot = {
        revision: this.revision,
        selectedCl: typeof hostState?.selectedCl == "number" ? hostState.selectedCl : null,
        selectedStackId: typeof hostState?.selectedStackId == "string" ? hostState.selectedStackId : null,
        continuousStackDiff: !!hostState?.continuousStackDiff
      };
      for (let listener of this.listeners)
        listener();
    }
  }, gerritUiStore = new GerritUIStore();
  function useGerritUIStore() {
    return (0, import_react.useSyncExternalStore)(
      gerritUiStore.subscribe,
      gerritUiStore.getSnapshot,
      gerritUiStore.getSnapshot
    );
  }
  function getHostGlobal() {
    return globalThis;
  }
  function getHostStateObject() {
    return getHostGlobal().state;
  }
  function createEmptyRebaseStatus() {
    return {
      hasConflict: !1,
      isOutdated: !1,
      isIndirectlyOutdated: !1,
      isParentMerged: !1,
      needsRebase: !1,
      parentCommit: null,
      parentClNumber: null,
      parentSubject: "",
      parentCurrentPs: 1,
      reason: "",
      conflicts: []
    };
  }
  function formatBranchDisplay(branch) {
    if (!branch)
      return "";
    let trimmed = String(branch).trim();
    return trimmed.startsWith(REFS_HEADS_PREFIX) ? trimmed.slice(REFS_HEADS_PREFIX.length) : trimmed;
  }
  function isChangeMerged(change) {
    return change ? String(change.status || "").toUpperCase() === CHANGE_STATUS_MERGED || !!change.is_merged : !1;
  }
  function isChangeAbandoned(change) {
    return change ? String(change.status || "").toUpperCase() === CHANGE_STATUS_ABANDONED || !!change.is_abandoned : !1;
  }
  var DEFAULT_HOST_BRIDGE = {
    getFallbackReviewersForCl(peer) {
      let host = getHostGlobal();
      return typeof host.getFallbackReviewersForCl == "function" ? host.getFallbackReviewersForCl(peer) || [] : Array.isArray(peer?.reviewers) ? peer.reviewers : [];
    },
    isClConflict(change, bundle) {
      let host = getHostGlobal();
      return typeof host.isClConflict == "function" ? !!host.isClConflict(change, bundle) : !!(change?.contains_git_conflicts || change?.mergeable === !1);
    },
    isClSubmittable(change) {
      let host = getHostGlobal();
      return typeof host.isClSubmittable == "function" ? !!host.isClSubmittable(change) : !!(change?.is_submittable || change?.submittable);
    },
    getChangeRebaseStatus(change, bundle) {
      let host = getHostGlobal();
      return typeof host.getPgChangeRebaseStatus == "function" ? host.getPgChangeRebaseStatus(change, bundle) : createEmptyRebaseStatus();
    },
    getBranchDisplay(branch) {
      let host = getHostGlobal();
      return typeof host.getPgBranchDisplay == "function" ? host.getPgBranchDisplay(branch) : formatBranchDisplay(branch);
    },
    getProjectDisplay(project) {
      let host = getHostGlobal();
      return typeof host.getPgProjectDisplay == "function" ? host.getPgProjectDisplay(project) : String(project || "").trim();
    },
    getChangeId(change) {
      let host = getHostGlobal();
      return typeof host.getPgChangeId == "function" ? host.getPgChangeId(change) : String(change?.change_id || "").trim();
    },
    findStackForClOrId(clNum, preferredStackId) {
      let host = getHostGlobal();
      return typeof host.findPgStackForClOrId == "function" ? host.findPgStackForClOrId(clNum, preferredStackId) : null;
    },
    getTopicPeers(change) {
      let host = getHostGlobal();
      return typeof host.getPgTopicPeers == "function" ? host.getPgTopicPeers(change) || [] : [];
    },
    getCherryPickPeers(change) {
      let host = getHostGlobal();
      return typeof host.getPgCherryPickPeers == "function" ? host.getPgCherryPickPeers(change) || [] : [];
    },
    getCherryPickGroup(change) {
      let host = getHostGlobal();
      return typeof host.getPgCherryPickGroup == "function" ? host.getPgCherryPickGroup(change) || [] : this.getCherryPickPeers(change);
    },
    getSubmittedTogetherPeers(change) {
      let host = getHostGlobal();
      return typeof host.getPgSubmittedTogetherPeers == "function" ? host.getPgSubmittedTogetherPeers(change) || [] : [];
    },
    getRelatedChangesTab(clNum) {
      let host = getHostGlobal();
      return typeof host.getPgRelatedChangesTab == "function" ? host.getPgRelatedChangesTab(clNum) : "chain";
    },
    canStackInterdiff(commits) {
      let host = getHostGlobal();
      return typeof host.canPgStackInterdiff == "function" ? !!host.canPgStackInterdiff(commits) : !1;
    },
    isStackInterdiffActive(commits, stackId) {
      let host = getHostGlobal();
      return typeof host.isPgStackInterdiffActive == "function" ? !!host.isPgStackInterdiffActive(commits, stackId) : !1;
    },
    getPersistedStackInterdiff(stackId) {
      let host = getHostGlobal();
      return typeof host.getPgPersistedStackInterdiff == "function" ? !!host.getPgPersistedStackInterdiff(stackId) : !1;
    },
    ensureStackInterdiffRestored(stack) {
      let host = getHostGlobal();
      typeof host.ensurePgStackInterdiffRestored == "function" && host.ensurePgStackInterdiffRestored(stack);
    },
    getSelectedPatchset(clNum) {
      let st = getHostStateObject();
      if (st?.selectedPatchsetByCl && st.selectedPatchsetByCl[clNum])
        return st.selectedPatchsetByCl[clNum];
      let host = getHostGlobal();
      return typeof host.getPgSelectedPatchset == "function" ? host.getPgSelectedPatchset(clNum) || {} : {};
    },
    getDiffBundle(clNum) {
      let st = getHostStateObject();
      return st?.diffsByCl && st.diffsByCl[clNum] || null;
    },
    isContinuousStackDiff() {
      return !!getHostStateObject()?.continuousStackDiff;
    },
    getSelectedClNumber() {
      let st = getHostStateObject(), raw = st?.selectedClNumber ?? st?.selectedCl;
      return typeof raw == "number" ? raw : null;
    },
    getSelectedStackId() {
      return getHostStateObject()?.selectedStackId ?? null;
    },
    isBatchModeActive() {
      let checked = getHostStateObject()?.checkedClNumbers;
      return !!(checked && checked.size > 0);
    },
    isClChecked(clNum) {
      let checked = getHostStateObject()?.checkedClNumbers;
      return !!(checked && checked.has(clNum));
    },
    isStackCommitReviewed(clNum) {
      let host = getHostGlobal();
      if (typeof host.isPgStackCommitReviewed == "function")
        return !!host.isPgStackCommitReviewed(clNum);
      let reviewed = getHostStateObject()?.reviewedStackCommits;
      return !!(reviewed && reviewed.has(Number(clNum)));
    },
    isStackCommitCollapsed(clNum) {
      let collapsed = getHostStateObject()?.collapsedStackCommits;
      return !!(collapsed && collapsed.has(Number(clNum)));
    },
    getStackSubmitSequence(stack, commits) {
      let host = getHostGlobal();
      if (typeof host.getPgStackSubmitSequence == "function")
        return host.getPgStackSubmitSequence(stack, commits);
      let list = commits || stack?.commits || [];
      return {
        items: [],
        total_commits: list.length,
        reviewed_commits: 0,
        submittable_commits: 0,
        seq_ready_count: 0,
        all_ready: !1,
        bottleneck_step: 1,
        bottleneck_commit: list[0] || null,
        bottleneck_reason: "Not ready"
      };
    }
  };
  function createHostBridge(overrides) {
    return overrides ? { ...DEFAULT_HOST_BRIDGE, ...overrides } : DEFAULT_HOST_BRIDGE;
  }

  // ../../../../../../../../execroot/_main/bazel-out/k8-fastbuild-ST-f481a530da89/bin/external/aemu+/tools/emu-dev-cli/src/ui/gerrit/components/ChangeHeader.tsx
  var import_jsx_runtime2 = __toESM(require_jsx_runtime()), BTN_REVERT_PREFIX = "btnHeaderRevert-", BTN_MARK_READY_PREFIX = "btnHeaderMarkReady-", BTN_MARK_WIP_PREFIX = "btnHeaderMarkWip-", BTN_PUBLISH_EDIT_PREFIX = "btnPublishEdit-", BTN_DISCARD_EDIT_PREFIX = "btnDiscardEdit-", BTN_EDIT_PREFIX = "btnHeaderEdit-", BTN_ARCH_MAP_PREFIX = "btnArchitectureMap-", WRAP_COPY_LINKS_PREFIX = "pgCopyLinksWrap-", BTN_COPY_LINKS_PREFIX = "btnCopyLinks-", DROPDOWN_COPY_LINKS_PREFIX = "pgCopyLinksDropdown-", BTN_STAR_PREFIX = "btnHeaderStar-", DEFAULT_GERRIT_HOST = "https://googleplex-android-review.git.corp.google.com";
  function ExpandSidebarButton({
    visible = !1,
    visibleCommitCount = 0
  }) {
    let disp = visible ? "inline-flex" : "none", lbl = visibleCommitCount > 0 ? `Queue (${visibleCommitCount})` : "Queue";
    return /* @__PURE__ */ (0, import_jsx_runtime2.jsxs)(
      "button",
      {
        id: "btnExpandSidebar",
        type: "button",
        className: "btn btn-expand-sidebar",
        htmlStyle: `display:${disp};`,
        htmlOnClick: "toggleSidebarCollapsed(false)",
        title: "Expand side panel ([)",
        "aria-label": "Expand side panel",
        children: [
          /* @__PURE__ */ (0, import_jsx_runtime2.jsxs)(
            "svg",
            {
              width: "14",
              height: "14",
              viewBox: "0 0 16 16",
              fill: "none",
              stroke: "currentColor",
              strokeWidth: "1.8",
              strokeLinecap: "round",
              strokeLinejoin: "round",
              "aria-hidden": "true",
              children: [
                /* @__PURE__ */ (0, import_jsx_runtime2.jsx)("rect", { x: "2", y: "2.5", width: "12", height: "11", rx: "2" }),
                /* @__PURE__ */ (0, import_jsx_runtime2.jsx)("line", { x1: "6.5", y1: "2.5", x2: "6.5", y2: "13.5" }),
                /* @__PURE__ */ (0, import_jsx_runtime2.jsx)("polyline", { points: "8.5 6.5 10 8 8.5 9.5" })
              ]
            }
          ),
          /* @__PURE__ */ (0, import_jsx_runtime2.jsx)("span", { id: "expandSidebarLabel", children: lbl })
        ]
      }
    );
  }
  function CommitAemuMapButton({
    clNumber,
    aemuMapUrl
  }) {
    if (!clNumber)
      return null;
    let iconText = "\u{1F310} Architecture Map".slice(0, 2);
    return /* @__PURE__ */ (0, import_jsx_runtime2.jsxs)(
      "a",
      {
        id: `${BTN_ARCH_MAP_PREFIX}${clNumber}`,
        className: "btn btn-aemu-map aemu-map-pill",
        href: aemuMapUrl,
        target: "_blank",
        rel: "noopener noreferrer",
        title: `Visualize ag/${clNumber} blast radius, call flows, and critical paths on go/aemu-map`,
        children: [
          /* @__PURE__ */ (0, import_jsx_runtime2.jsx)("span", { className: "aemu-map-icon", "aria-hidden": "true", children: iconText }),
          " Architecture Map ",
          /* @__PURE__ */ (0, import_jsx_runtime2.jsx)("span", { className: "aemu-map-ext-arrow", "aria-hidden": "true", children: "\u2197" })
        ]
      }
    );
  }
  function SrSummaryChip({
    clNumber,
    srSummary
  }) {
    let icon = srSummary.status === "submittable" ? "\u2713" : "\u2715", textLabel = (srSummary.label || "").replace(/^[✓✕]\s*/, "");
    return /* @__PURE__ */ (0, import_jsx_runtime2.jsxs)(
      "button",
      {
        type: "button",
        className: `pg-sr-summary-chip ${srSummary.cls}`,
        htmlOnClick: `event.stopPropagation(); openPgSubmitRequirementsAuditDialog(${clNumber})`,
        title: `${srSummary.tip} \xB7 Click to audit requirements (Shift+Z)`,
        children: [
          /* @__PURE__ */ (0, import_jsx_runtime2.jsx)("span", { className: "pg-sr-summary-icon", "aria-hidden": "true", children: icon }),
          /* @__PURE__ */ (0, import_jsx_runtime2.jsx)("span", { children: textLabel })
        ]
      }
    );
  }
  function buildCopyLinkEntries(clNum, curPs, info) {
    return [
      { key: "change_id", label: "Change-Id", val: info.changeId },
      { key: "commit", label: `Commit (PS ${curPs})`, val: info.commitSha },
      ...info.repoDownloadCmd ? [
        {
          key: "repo_download",
          label: `Repo Download (PS ${curPs})`,
          val: info.repoDownloadCmd
        }
      ] : [],
      ...info.gitCheckoutCmd ? [
        {
          key: "git_checkout",
          label: `Git Checkout (PS ${curPs})`,
          val: info.gitCheckoutCmd
        }
      ] : [],
      ...info.gitCherryPickCmd ? [
        {
          key: "cherry_pick_cmd",
          label: `Git Cherry-Pick (PS ${curPs})`,
          val: info.gitCherryPickCmd
        }
      ] : [],
      ...info.filePaths && info.filePaths.length > 0 ? [
        {
          key: "files",
          label: `Files (${info.filePaths.length})`,
          val: info.filePathsText
        }
      ] : [],
      ...info.cherryPickOfChange ? [
        {
          key: "cherry_pick_of",
          label: "Cherry pick of",
          val: info.cherryPickOfRef
        }
      ] : [],
      ...info.revertOfChange ? [
        {
          key: "revert_of",
          label: "Revert of",
          val: String(info.revertOfChange)
        }
      ] : [],
      { key: "shortlink", label: "ag/ shortlink", val: info.shortlink },
      { key: "number", label: "Change number", val: String(clNum) },
      { key: "url", label: "Gerrit URL", val: info.gerritUrl },
      { key: "title_url", label: "Title & Link", val: info.titleAndUrl },
      { key: "markdown", label: "Markdown link", val: info.markdown }
    ].filter((item) => !!item.val);
  }
  function CopyLinksMenu({
    change,
    bundle = null,
    info,
    isOpen = !1
  }) {
    if (!change || !change.number)
      return null;
    let clNum = Number(change.number), curPs = Number(bundle?.patch_set || change.patch_set || 1), items = buildCopyLinkEntries(clNum, curPs, info);
    return /* @__PURE__ */ (0, import_jsx_runtime2.jsxs)("div", { id: `${WRAP_COPY_LINKS_PREFIX}${clNum}`, className: "pg-copy-links-wrap", children: [
      /* @__PURE__ */ (0, import_jsx_runtime2.jsxs)(
        "button",
        {
          id: `${BTN_COPY_LINKS_PREFIX}${clNum}`,
          type: "button",
          className: `pg-icon-btn pg-btn-copy-links ${isOpen ? "is-active" : ""}`,
          htmlOnClick: `event.stopPropagation(); togglePgCopyLinksMenu(${clNum})`,
          title: `Copy links and references for ag/${clNum} (Shortcut: Shift+L)`,
          "aria-label": "Copy links and references",
          "aria-expanded": isOpen ? "true" : "false",
          "aria-haspopup": "true",
          children: [
            /* @__PURE__ */ (0, import_jsx_runtime2.jsx)("span", { className: "pg-copy-icon", "aria-hidden": "true", children: "\u29C9" }),
            /* @__PURE__ */ (0, import_jsx_runtime2.jsx)("span", { className: "pg-copy-chevron", "aria-hidden": "true", children: "\u25BE" })
          ]
        }
      ),
      /* @__PURE__ */ (0, import_jsx_runtime2.jsxs)(
        "div",
        {
          id: `${DROPDOWN_COPY_LINKS_PREFIX}${clNum}`,
          className: `pg-copy-links-dropdown ${isOpen ? "is-open" : ""}`,
          htmlOnClick: "event.stopPropagation()",
          role: "menu",
          "aria-label": "Copy references",
          children: [
            /* @__PURE__ */ (0, import_jsx_runtime2.jsxs)("div", { className: "pg-copy-links-header", children: [
              /* @__PURE__ */ (0, import_jsx_runtime2.jsx)("span", { className: "pg-copy-links-title", children: "\u{1F4CB} Copy Links & References" }),
              /* @__PURE__ */ (0, import_jsx_runtime2.jsx)(
                "button",
                {
                  type: "button",
                  className: "pg-copy-close-btn",
                  htmlOnClick: "event.stopPropagation(); closePgCopyLinksMenu()",
                  title: "Close menu (Esc)",
                  "aria-label": "Close",
                  children: "\u2715"
                }
              )
            ] }),
            /* @__PURE__ */ (0, import_jsx_runtime2.jsx)("div", { className: "pg-copy-links-items", children: items.map((item) => /* @__PURE__ */ (0, import_jsx_runtime2.jsxs)(
              "div",
              {
                className: "pg-copy-link-item",
                "data-key": item.key,
                children: [
                  /* @__PURE__ */ (0, import_jsx_runtime2.jsxs)("div", { className: "pg-copy-link-meta", children: [
                    /* @__PURE__ */ (0, import_jsx_runtime2.jsx)("span", { className: "pg-copy-link-label", children: item.label }),
                    /* @__PURE__ */ (0, import_jsx_runtime2.jsx)("span", { className: "pg-copy-link-value", title: item.val, children: item.val })
                  ] }),
                  /* @__PURE__ */ (0, import_jsx_runtime2.jsx)(
                    "button",
                    {
                      type: "button",
                      className: "pg-btn-copy-pill",
                      htmlOnClick: `event.stopPropagation(); copyPgMetaField(${clNum}, '${item.key}', this)`,
                      title: `Copy ${item.label}`,
                      children: "\u{1F4CB} Copy"
                    }
                  )
                ]
              },
              item.key
            )) }),
            /* @__PURE__ */ (0, import_jsx_runtime2.jsxs)("div", { className: "pg-copy-links-footer", children: [
              info.gitilesUrl ? /* @__PURE__ */ (0, import_jsx_runtime2.jsx)(
                "a",
                {
                  className: "pg-copy-footer-link",
                  href: info.gitilesUrl,
                  target: "_blank",
                  rel: "noopener noreferrer",
                  title: "Open commit in Gitiles \u2197",
                  children: "Gitiles \u2197"
                }
              ) : null,
              info.codeSearchUrl ? /* @__PURE__ */ (0, import_jsx_runtime2.jsx)(
                "a",
                {
                  className: "pg-copy-footer-link",
                  href: info.codeSearchUrl,
                  target: "_blank",
                  rel: "noopener noreferrer",
                  title: "Search Change-Id in Code Search \u2197",
                  children: "Code Search \u2197"
                }
              ) : null,
              /* @__PURE__ */ (0, import_jsx_runtime2.jsx)(
                "button",
                {
                  type: "button",
                  className: "pg-copy-footer-btn",
                  htmlOnClick: `event.stopPropagation(); closePgCopyLinksMenu(); openPgDownloadDialog(${clNum})`,
                  title: "Open full Download Patch & Checkout dialog (Shortcut: d)",
                  children: "\u2B07 Download commands\u2026"
                }
              )
            ] })
          ]
        }
      )
    ] });
  }
  function HeaderActionButtons({
    change,
    hasActiveEdit = !1
  }) {
    if (!change)
      return null;
    let num = Number(change.number), isAbandoned = isChangeAbandoned(change), isMerged = isChangeMerged(change), isWip = !!change.work_in_progress, isEditable = !isAbandoned && !isMerged;
    return /* @__PURE__ */ (0, import_jsx_runtime2.jsxs)("div", { className: "pg-header-action-group", children: [
      hasActiveEdit ? /* @__PURE__ */ (0, import_jsx_runtime2.jsxs)(import_jsx_runtime2.Fragment, { children: [
        /* @__PURE__ */ (0, import_jsx_runtime2.jsx)(
          "span",
          {
            className: "pg-edit-in-progress-badge",
            title: `Active change edit exists for ag/${num}`,
            children: "\u270F\uFE0F Edit in progress"
          }
        ),
        /* @__PURE__ */ (0, import_jsx_runtime2.jsxs)(
          "button",
          {
            type: "button",
            id: `${BTN_PUBLISH_EDIT_PREFIX}${num}`,
            className: "btn pg-btn-header-action pg-btn-publish-edit",
            htmlOnClick: `event.stopPropagation(); publishActiveChangeEdit(${num})`,
            title: "Publish change edit as new patchset (notify: OWNER)",
            children: [
              /* @__PURE__ */ (0, import_jsx_runtime2.jsx)("span", { className: "pg-btn-icon", "aria-hidden": "true", children: "\u2713" }),
              " ",
              "Publish Edit"
            ]
          }
        ),
        /* @__PURE__ */ (0, import_jsx_runtime2.jsxs)(
          "button",
          {
            type: "button",
            id: `${BTN_DISCARD_EDIT_PREFIX}${num}`,
            className: "btn pg-btn-header-action pg-btn-discard-edit",
            htmlOnClick: `event.stopPropagation(); discardActiveChangeEdit(${num})`,
            title: "Discard active change edit",
            children: [
              /* @__PURE__ */ (0, import_jsx_runtime2.jsx)("span", { className: "pg-btn-icon", "aria-hidden": "true", children: "\u2715" }),
              " ",
              "Discard Edit"
            ]
          }
        )
      ] }) : null,
      isEditable ? isWip ? /* @__PURE__ */ (0, import_jsx_runtime2.jsxs)(
        "button",
        {
          type: "button",
          id: `${BTN_MARK_READY_PREFIX}${num}`,
          className: "btn pg-btn-header-action pg-btn-ready",
          htmlOnClick: `event.stopPropagation(); openPgSetReadyDialog(${num})`,
          title: `Mark change ag/${num} as active / ready for review (Shortcut: Alt+W)`,
          children: [
            /* @__PURE__ */ (0, import_jsx_runtime2.jsx)("span", { className: "pg-btn-icon", "aria-hidden": "true", children: "\u2713" }),
            " ",
            "Mark as Active"
          ]
        }
      ) : /* @__PURE__ */ (0, import_jsx_runtime2.jsxs)(
        "button",
        {
          type: "button",
          id: `${BTN_MARK_WIP_PREFIX}${num}`,
          className: "btn pg-btn-header-action pg-btn-wip",
          htmlOnClick: `event.stopPropagation(); openPgSetWipDialog(${num})`,
          title: `Mark change ag/${num} as Work in Progress (Shortcut: Alt+W)`,
          children: [
            /* @__PURE__ */ (0, import_jsx_runtime2.jsx)("span", { className: "pg-btn-icon", "aria-hidden": "true", children: "\u{1F6A7}" }),
            " ",
            "Mark as WIP"
          ]
        }
      ) : null,
      isEditable ? /* @__PURE__ */ (0, import_jsx_runtime2.jsxs)(
        "button",
        {
          type: "button",
          id: `${BTN_EDIT_PREFIX}${num}`,
          className: "btn pg-btn-header-action pg-btn-edit-mode",
          htmlOnClick: `event.stopPropagation(); openPgFileEditor(${num})`,
          title: "Edit files in browser (Shortcut: e)",
          children: [
            /* @__PURE__ */ (0, import_jsx_runtime2.jsx)("span", { className: "pg-btn-icon", "aria-hidden": "true", children: "\u270F\uFE0F" }),
            " ",
            "Edit"
          ]
        }
      ) : null,
      /* @__PURE__ */ (0, import_jsx_runtime2.jsxs)(
        "button",
        {
          type: "button",
          className: "btn pg-btn-header-action pg-btn-followup",
          htmlOnClick: `event.stopPropagation(); openPgFollowUpDialog(${num})`,
          title: `Create follow-up change stacked on ag/${num} (Shortcut: Shift+U)`,
          children: [
            /* @__PURE__ */ (0, import_jsx_runtime2.jsx)("span", { className: "pg-btn-icon", "aria-hidden": "true", children: "\u21B3" }),
            " ",
            "Follow-Up"
          ]
        }
      ),
      /* @__PURE__ */ (0, import_jsx_runtime2.jsxs)(
        "button",
        {
          type: "button",
          className: "btn pg-btn-header-action pg-btn-included-in",
          htmlOnClick: `event.stopPropagation(); openPgIncludedInDialog(${num})`,
          title: `Inspect branches and tags containing change ag/${num} (Shortcut: Shift+G)`,
          children: [
            /* @__PURE__ */ (0, import_jsx_runtime2.jsx)("span", { className: "pg-btn-icon", "aria-hidden": "true", children: "\u{1F3F7}\uFE0F" }),
            " ",
            "Included In"
          ]
        }
      ),
      /* @__PURE__ */ (0, import_jsx_runtime2.jsxs)(
        "button",
        {
          type: "button",
          className: "btn pg-btn-header-action",
          htmlOnClick: `event.stopPropagation(); openPgDownloadDialog(${num})`,
          title: `Download patch & checkout commands for ag/${num} (Shortcut: d)`,
          children: [
            /* @__PURE__ */ (0, import_jsx_runtime2.jsx)("span", { className: "pg-btn-icon", "aria-hidden": "true", children: "\u2B07" }),
            " ",
            "Download"
          ]
        }
      ),
      isEditable ? /* @__PURE__ */ (0, import_jsx_runtime2.jsxs)(
        "button",
        {
          type: "button",
          className: "btn pg-btn-header-action",
          htmlOnClick: `event.stopPropagation(); openPgRebaseDialog(${num})`,
          title: `Rebase change ag/${num} on parent branch or base (Shortcut: Alt+U)`,
          children: [
            /* @__PURE__ */ (0, import_jsx_runtime2.jsx)("span", { className: "pg-btn-icon", "aria-hidden": "true", children: "\u21BB" }),
            " ",
            "Rebase"
          ]
        }
      ) : null,
      /* @__PURE__ */ (0, import_jsx_runtime2.jsxs)(
        "button",
        {
          type: "button",
          className: "btn pg-btn-header-action pg-btn-cherrypick",
          htmlOnClick: `event.stopPropagation(); openPgCherryPickDialog(${num})`,
          title: `Cherry pick change ag/${num} to another branch (Shortcut: Shift+C)`,
          children: [
            /* @__PURE__ */ (0, import_jsx_runtime2.jsx)("span", { className: "pg-btn-icon", "aria-hidden": "true", children: "\u{1F352}" }),
            " ",
            "Cherry Pick"
          ]
        }
      ),
      isMerged ? /* @__PURE__ */ (0, import_jsx_runtime2.jsxs)(
        "button",
        {
          type: "button",
          id: `${BTN_REVERT_PREFIX}${num}`,
          className: "btn pg-btn-header-action pg-btn-revert",
          htmlOnClick: `event.stopPropagation(); openPgRevertDialog(${num})`,
          title: `Revert merged change ag/${num} (creates revert change with notify: OWNER)`,
          children: [
            /* @__PURE__ */ (0, import_jsx_runtime2.jsx)("span", { className: "pg-btn-icon", "aria-hidden": "true", children: "\u21BA" }),
            " ",
            "Revert"
          ]
        }
      ) : isAbandoned ? /* @__PURE__ */ (0, import_jsx_runtime2.jsxs)(
        "button",
        {
          type: "button",
          className: "btn pg-btn-header-action pg-btn-restore",
          htmlOnClick: `event.stopPropagation(); openPgRestoreDialog(${num})`,
          title: `Restore abandoned change ag/${num}`,
          children: [
            /* @__PURE__ */ (0, import_jsx_runtime2.jsx)("span", { className: "pg-btn-icon", "aria-hidden": "true", children: "\u21BA" }),
            " ",
            "Restore"
          ]
        }
      ) : /* @__PURE__ */ (0, import_jsx_runtime2.jsxs)(
        "button",
        {
          type: "button",
          className: "btn pg-btn-header-action pg-btn-abandon",
          htmlOnClick: `event.stopPropagation(); openPgAbandonDialog(${num})`,
          title: `Abandon change ag/${num}`,
          children: [
            /* @__PURE__ */ (0, import_jsx_runtime2.jsx)("span", { className: "pg-btn-icon", "aria-hidden": "true", children: "\u2298" }),
            " ",
            "Abandon"
          ]
        }
      )
    ] });
  }
  function ChangeHeaderTitleBar({
    change,
    bundle = null,
    statusInfo,
    srSummary,
    refInfo,
    isCopyMenuOpen = !1,
    isStarred = !1,
    isViewerTurn = !1,
    expandSidebarVisible = !1,
    visibleCommitCount = 0
  }) {
    let num = Number(change.number), gerritUrl = change.url || `${DEFAULT_GERRIT_HOST}/c/${num}`, isPureRebase = !!bundle?.is_pure_rebase, curPs = Number(change.patch_set || 1), isHeaderUrgent = !!(isViewerTurn && (change.attention_waiting_display && /[dw]/.test(change.attention_waiting_display) || Number(change.unresolved_comments || 0) > 0)), headerWaitNote = change.attention_waiting_display ? ` (waiting ${change.attention_waiting_display})` : "", turnTitle = `Your turn in Gerrit Attention Set${isHeaderUrgent ? ` (Urgent${headerWaitNote})` : headerWaitNote}`;
    return /* @__PURE__ */ (0, import_jsx_runtime2.jsxs)("div", { className: "pg-header-title", children: [
      /* @__PURE__ */ (0, import_jsx_runtime2.jsx)(
        ExpandSidebarButton,
        {
          visible: expandSidebarVisible,
          visibleCommitCount
        }
      ),
      /* @__PURE__ */ (0, import_jsx_runtime2.jsx)(
        "span",
        {
          className: `pg-status-badge ${statusInfo.cls}`,
          title: statusInfo.tip,
          children: statusInfo.label
        }
      ),
      statusInfo.secondaryCls && statusInfo.secondaryLabel ? /* @__PURE__ */ (0, import_jsx_runtime2.jsx)(
        "span",
        {
          className: `pg-status-badge ${statusInfo.secondaryCls}`,
          title: statusInfo.secondaryTip || "",
          children: statusInfo.secondaryLabel
        }
      ) : null,
      isViewerTurn ? /* @__PURE__ */ (0, import_jsx_runtime2.jsxs)(
        "span",
        {
          className: `pg-status-badge status-attention is-pulsing ${isHeaderUrgent ? "is-urgent" : ""}`,
          title: turnTitle,
          children: [
            /* @__PURE__ */ (0, import_jsx_runtime2.jsx)("span", { className: "pg-turn-dot", "aria-hidden": "true" }),
            " Your Turn"
          ]
        }
      ) : null,
      isPureRebase ? /* @__PURE__ */ (0, import_jsx_runtime2.jsx)(
        "span",
        {
          className: "pg-status-badge status-rebase",
          title: `PS${curPs - 1}\u2192PS${curPs} is a pure rebase (0 code changes)`,
          children: "Pure Rebase"
        }
      ) : null,
      /* @__PURE__ */ (0, import_jsx_runtime2.jsx)(SrSummaryChip, { clNumber: num, srSummary }),
      /* @__PURE__ */ (0, import_jsx_runtime2.jsxs)(
        "button",
        {
          id: `${BTN_STAR_PREFIX}${num}`,
          type: "button",
          className: `pg-header-star-btn ${isStarred ? "is-starred" : ""}`,
          htmlOnClick: `togglePgStarChange(${num}, event)`,
          title: isStarred ? `Unstar change ag/${num} (*)` : `Star change ag/${num} (*)`,
          "aria-label": isStarred ? "Starred" : "Not starred",
          children: [
            /* @__PURE__ */ (0, import_jsx_runtime2.jsx)("span", { className: "pg-star-icon", "aria-hidden": "true", children: isStarred ? "\u2605" : "\u2606" }),
            /* @__PURE__ */ (0, import_jsx_runtime2.jsx)("span", { className: "pg-star-label", children: isStarred ? "Starred" : "Star" })
          ]
        }
      ),
      /* @__PURE__ */ (0, import_jsx_runtime2.jsx)(
        "a",
        {
          className: "pg-change-number commit-cl-id",
          href: gerritUrl,
          target: "_blank",
          rel: "noopener noreferrer",
          title: `Open ag/${num} in Gerrit \u2197`,
          children: num
        }
      ),
      /* @__PURE__ */ (0, import_jsx_runtime2.jsx)("span", { className: "pg-header-colon", children: ":" }),
      /* @__PURE__ */ (0, import_jsx_runtime2.jsx)("h1", { className: "pg-header-subject", title: change.subject || "", children: change.subject || "" }),
      /* @__PURE__ */ (0, import_jsx_runtime2.jsx)(
        CopyLinksMenu,
        {
          change,
          bundle,
          info: refInfo,
          isOpen: isCopyMenuOpen
        }
      )
    ] });
  }
  function renderExpandSidebarButtonHtml(visible = !1, visibleCommitCount = 0) {
    return renderReactToHtml(
      /* @__PURE__ */ (0, import_jsx_runtime2.jsx)(
        ExpandSidebarButton,
        {
          visible,
          visibleCommitCount
        }
      )
    );
  }
  function renderCommitAemuMapButtonHtml(clNumber, aemuMapUrl) {
    return renderReactToHtml(
      /* @__PURE__ */ (0, import_jsx_runtime2.jsx)(CommitAemuMapButton, { clNumber, aemuMapUrl })
    );
  }
  function renderPgSrSummaryChipHtml(clNumber, srSummary) {
    return renderReactToHtml(
      /* @__PURE__ */ (0, import_jsx_runtime2.jsx)(SrSummaryChip, { clNumber, srSummary })
    );
  }
  function renderPgCopyLinksMenuHtml(change, bundle, info, isOpen = !1) {
    return renderReactToHtml(
      /* @__PURE__ */ (0, import_jsx_runtime2.jsx)(
        CopyLinksMenu,
        {
          change,
          bundle,
          info,
          isOpen
        }
      )
    );
  }
  function renderPgHeaderActionButtonsHtml(change, hasActiveEdit = !1) {
    return renderReactToHtml(
      /* @__PURE__ */ (0, import_jsx_runtime2.jsx)(HeaderActionButtons, { change, hasActiveEdit })
    );
  }
  function renderPgChangeHeaderTitleBarHtml(props) {
    return renderReactToHtml(/* @__PURE__ */ (0, import_jsx_runtime2.jsx)(ChangeHeaderTitleBar, { ...props }));
  }

  // ../../../../../../../../execroot/_main/bazel-out/k8-fastbuild-ST-f481a530da89/bin/external/aemu+/tools/emu-dev-cli/src/ui/gerrit/components/ChecksAndReplyView.tsx
  var import_jsx_runtime3 = __toESM(require_jsx_runtime()), PRESUBMIT_KIND_LABELS = {
    build: "BUILD",
    test: "TEST",
    preflight: "PREFLIGHT",
    test_module: "MODULE"
  };
  function PresubmitStatusPill(props) {
    let { t } = props, st = t.status || "queued";
    if (st === "failed") {
      let txt = t.raw_status === "TIMED_OUT" ? "\u2717 TIMEOUT" : t.raw_status === "INFRA_ERROR" ? "\u2717 INFRA ERR" : "\u2717 FAIL";
      return /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("span", { className: "ps-status-pill st-failed", children: txt });
    }
    if (st === "warning")
      return /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("span", { className: "ps-status-pill st-warning", children: "\u26A0 WARN" });
    if (st === "running")
      return /* @__PURE__ */ (0, import_jsx_runtime3.jsxs)("span", { className: "ps-status-pill st-running", children: [
        /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("span", { className: "pv-mini-spinner" }),
        " RUN"
      ] });
    if (st === "queued")
      return /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("span", { className: "ps-status-pill st-queued", children: "\u23F3 QUEUED" });
    if (st === "passed" && t.cached)
      return /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("span", { className: "ps-status-pill st-cached", children: "\u2713 CACHED" });
    if (st === "passed")
      return /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("span", { className: "ps-status-pill st-passed", children: "\u2713 PASS" });
    let skipTxt = t.raw_status === "DEPENDENCY_FAILED" ? "\u2014 DEP FAIL" : "\u2014 SKIP";
    return /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("span", { className: "ps-status-pill st-skipped", children: skipTxt });
  }
  function SinglePresubmitTargetRow(props) {
    let { t, fallbackChecksUrl } = props, mainUrl = t.url || fallbackChecksUrl || "#", kindLabel = t.kind && PRESUBMIT_KIND_LABELS[t.kind] || "TARGET", isWarnOrNonBlock = t.blocking_level === "WARN" || t.blocking_level === "NON_BLOCKING", tipParts = [`[${kindLabel}] ${t.name}`, `Status: ${t.raw_status || t.status}`];
    return t.subtitle && tipParts.push(t.subtitle), t.artifact_id && tipParts.push(`ID: ${t.artifact_id}`), t.duration_display && tipParts.push(`Duration: ${t.duration_display}`), /* @__PURE__ */ (0, import_jsx_runtime3.jsxs)("div", { className: `ps-target-row status-${t.status || "queued"}`, children: [
      /* @__PURE__ */ (0, import_jsx_runtime3.jsxs)(
        "a",
        {
          className: "ps-target-main-link",
          href: mainUrl,
          target: "_blank",
          rel: "noopener noreferrer",
          title: tipParts.join(" \xB7 "),
          onClick: "event.stopPropagation()",
          children: [
            /* @__PURE__ */ (0, import_jsx_runtime3.jsx)(PresubmitStatusPill, { t }),
            /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("span", { className: "ps-kind-tag", children: kindLabel }),
            /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("span", { className: "ps-target-name", children: `${t.name} \u2197` }),
            t.waiting_on_build ? /* @__PURE__ */ (0, import_jsx_runtime3.jsx)(
              "span",
              {
                className: "ps-meta-tag",
                title: "Waiting for prerequisite build target to finish",
                children: "\u23F3 waiting on build"
              }
            ) : isWarnOrNonBlock ? /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("span", { className: "ps-meta-tag", children: t.blocking_level === "WARN" ? "warn-only" : "non-blocking" }) : null,
            t.subtitle && t.subtitle !== t.name ? /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("span", { className: "ps-target-sub", children: `\xB7 ${t.subtitle}` }) : null
          ]
        }
      ),
      /* @__PURE__ */ (0, import_jsx_runtime3.jsxs)("div", { className: "ps-target-right", children: [
        t.duration_display ? /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("span", { children: t.duration_display }) : null,
        t.artifact_id ? /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("span", { className: "ps-artifact-tag", title: "Android Build / AnTS Invocation ID", children: t.artifact_id }) : null,
        t.secondary_url ? /* @__PURE__ */ (0, import_jsx_runtime3.jsx)(
          "a",
          {
            className: "ps-target-sec-link",
            href: t.secondary_url,
            target: "_blank",
            rel: "noopener noreferrer",
            onClick: "event.stopPropagation()",
            children: `${t.secondary_label || "Details"} \u2197`
          }
        ) : null
      ] })
    ] });
  }
  function ChecksTabButton(props) {
    let { num, isActive, badgeClass = "", badgeText = "" } = props;
    return /* @__PURE__ */ (0, import_jsx_runtime3.jsxs)(
      "a",
      {
        href: "javascript:void(0)",
        id: `pgTabChecks-${num}`,
        className: `pg-tab ${isActive ? "active" : "pg-tab-link"}`,
        onClick: `selectPgChangeTab(${num}, 'checks')`,
        title: "View TreeHugger & Presubmit Checks (Shortcut: Shift+K)",
        children: [
          "Checks ",
          badgeText ? /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("span", { className: `pg-checks-tab-badge ${badgeClass}`, children: badgeText }) : null
        ]
      }
    );
  }
  function ChecksControls(props) {
    let { num, isRefreshing, showStagePrBtn, workplanUrl, workplanId, checksUrl } = props;
    return /* @__PURE__ */ (0, import_jsx_runtime3.jsxs)("div", { style: { display: "inline-flex", alignItems: "center", gap: "10px", flexWrap: "wrap" }, children: [
      showStagePrBtn ? /* @__PURE__ */ (0, import_jsx_runtime3.jsx)(
        "button",
        {
          type: "button",
          className: "pg-btn-subtle",
          onClick: "event.stopPropagation(); toggleStagedPr(true)",
          title: "Stage Presubmit-Ready +1 (Shortcut: Shift+P)",
          children: "\u2611 Stage PR +1"
        }
      ) : null,
      workplanUrl ? /* @__PURE__ */ (0, import_jsx_runtime3.jsx)(
        "a",
        {
          className: "ag-link",
          style: { fontSize: "12px" },
          href: workplanUrl,
          target: "_blank",
          rel: "noopener noreferrer",
          onClick: "event.stopPropagation()",
          children: `Workplan ${workplanId || ""} \u2197`
        }
      ) : null,
      checksUrl ? /* @__PURE__ */ (0, import_jsx_runtime3.jsx)(
        "a",
        {
          className: "ag-link",
          style: { fontSize: "12px" },
          href: checksUrl,
          target: "_blank",
          rel: "noopener noreferrer",
          onClick: "event.stopPropagation()",
          children: "Gerrit Checks \u2197"
        }
      ) : null,
      /* @__PURE__ */ (0, import_jsx_runtime3.jsx)(
        "button",
        {
          type: "button",
          className: "pg-btn-subtle",
          disabled: isRefreshing,
          onClick: `fetchPresubmitTargetsForCl(${num}, { forceRefresh: true })`,
          title: "Refresh TreeHugger presubmit targets",
          children: isRefreshing ? /* @__PURE__ */ (0, import_jsx_runtime3.jsxs)(import_jsx_runtime3.Fragment, { children: [
            /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("span", { className: "btn-spinner" }),
            " Syncing\u2026"
          ] }) : "\u21BB Refresh"
        }
      )
    ] });
  }
  function CheckRunLogDrawer(props) {
    let { num, target, isLogOpen, rawLogSearch, visibleLines } = props, safeTargetName = escapeJsQuote(target.name || ""), kindLabel = target.kind && PRESUBMIT_KIND_LABELS[target.kind] || "TARGET", statusText = target.raw_status || target.status || "UNKNOWN", logSearchLower = String(rawLogSearch || "").trim().toLowerCase();
    return /* @__PURE__ */ (0, import_jsx_runtime3.jsxs)(
      "div",
      {
        className: `pg-checks-log-drawer ${isLogOpen ? "is-open" : ""}`,
        id: `pgChecksLogDrawer-${num}`,
        style: isLogOpen ? void 0 : { display: "none" },
        children: [
          /* @__PURE__ */ (0, import_jsx_runtime3.jsxs)("div", { className: "pg-checks-log-header", children: [
            /* @__PURE__ */ (0, import_jsx_runtime3.jsxs)("div", { className: "pg-checks-log-title-wrap", children: [
              /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("span", { className: "pg-checks-log-icon", children: "\u{1F4DC}" }),
              /* @__PURE__ */ (0, import_jsx_runtime3.jsxs)("span", { className: "pg-checks-log-title", children: [
                "Console Run Log: ",
                /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("code", { children: target.name })
              ] }),
              /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("span", { className: "pg-checks-kind-badge", children: kindLabel }),
              /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("span", { className: `pg-checks-log-status-tag ${target.status || ""}`, children: statusText })
            ] }),
            /* @__PURE__ */ (0, import_jsx_runtime3.jsxs)("div", { className: "pg-checks-log-actions", children: [
              /* @__PURE__ */ (0, import_jsx_runtime3.jsxs)("div", { className: "pg-checks-log-search-wrap", children: [
                /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("span", { className: "pg-checks-log-search-icon", children: "\u{1F50D}" }),
                /* @__PURE__ */ (0, import_jsx_runtime3.jsx)(
                  "input",
                  {
                    type: "text",
                    id: `pgCheckLogFilterInput-${num}`,
                    className: "pg-checks-log-search-input",
                    value: rawLogSearch || "",
                    placeholder: "Filter log lines...",
                    onInput: `onPgCheckLogSearchInput(${num}, this.value)`,
                    "aria-label": "Filter log lines"
                  }
                ),
                logSearchLower ? /* @__PURE__ */ (0, import_jsx_runtime3.jsx)(
                  "button",
                  {
                    type: "button",
                    className: "pg-checks-log-search-clear",
                    onClick: `clearPgCheckLogSearch(${num})`,
                    title: "Clear filter",
                    children: "\u2715"
                  }
                ) : null
              ] }),
              /* @__PURE__ */ (0, import_jsx_runtime3.jsx)(
                "button",
                {
                  type: "button",
                  className: "pg-btn-subtle",
                  onClick: `copyPgCheckLog(${num}, '${safeTargetName}')`,
                  title: "Copy full console log",
                  children: "\u{1F4CB} Copy"
                }
              ),
              /* @__PURE__ */ (0, import_jsx_runtime3.jsx)(
                "button",
                {
                  type: "button",
                  className: "pg-btn-subtle",
                  onClick: `downloadPgCheckLog(${num}, '${safeTargetName}')`,
                  title: "Download log as .log file",
                  children: "\u2B07 Download"
                }
              ),
              /* @__PURE__ */ (0, import_jsx_runtime3.jsx)(
                "button",
                {
                  type: "button",
                  className: "pg-btn-subtle",
                  onClick: `togglePgCheckLogDrawer(${num})`,
                  title: "Close log drawer",
                  children: "\u2715"
                }
              )
            ] })
          ] }),
          /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("div", { className: "pg-checks-log-body", id: `pgChecksLogBody-${num}`, children: /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("div", { className: "pg-checks-log-terminal", children: visibleLines.length === 0 ? /* @__PURE__ */ (0, import_jsx_runtime3.jsxs)("div", { className: "pg-checks-log-empty", children: [
            `No log lines matching "${logSearchLower}". `,
            /* @__PURE__ */ (0, import_jsx_runtime3.jsx)(
              "button",
              {
                type: "button",
                className: "pg-btn-subtle",
                style: { marginLeft: "6px" },
                onClick: `clearPgCheckLogSearch(${num})`,
                children: "Clear Filter"
              }
            )
          ] }) : visibleLines.map((l, idx) => /* @__PURE__ */ (0, import_jsx_runtime3.jsxs)("div", { className: "pg-log-line", children: [
            /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("span", { className: "pg-log-line-num", children: idx + 1 }),
            /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("span", { className: `pg-log-line-text ${l.type ? `pg-log-${l.type}` : ""}`, children: l.text })
          ] }, idx)) }) })
        ]
      }
    );
  }
  function CheckDetailCard(props) {
    let { num, ps, target: t, checksUrl, isLogOpen, logDrawerHtml } = props, kindLabel = t.kind && PRESUBMIT_KIND_LABELS[t.kind] || "TARGET", mainUrl = t.url || checksUrl || "#", statusText = t.raw_status || t.status || "UNKNOWN", durText = t.duration_display || "\u2014", artifactId = t.artifact_id || "", isFailed = t.status === "failed" || t.status === "warning", isRunning = t.status === "running" || t.status === "queued", isPassed = t.status === "passed", safeTargetName = escapeJsQuote(t.name || "");
    return /* @__PURE__ */ (0, import_jsx_runtime3.jsxs)("div", { className: "pg-checks-detail-card", children: [
      /* @__PURE__ */ (0, import_jsx_runtime3.jsxs)("div", { className: "pg-checks-detail-head", children: [
        /* @__PURE__ */ (0, import_jsx_runtime3.jsxs)("div", { style: { display: "flex", flexDirection: "column", gap: "4px" }, children: [
          /* @__PURE__ */ (0, import_jsx_runtime3.jsxs)("div", { style: { display: "flex", alignItems: "center", gap: "8px", flexWrap: "wrap" }, children: [
            /* @__PURE__ */ (0, import_jsx_runtime3.jsx)(PresubmitStatusPill, { t }),
            /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("span", { className: "pg-checks-kind-badge", children: kindLabel }),
            /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("span", { className: "pg-checks-detail-title", children: t.name })
          ] }),
          t.subtitle && t.subtitle !== t.name ? /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("span", { style: { fontSize: "12px", color: "var(--g-muted)" }, children: t.subtitle }) : null
        ] }),
        /* @__PURE__ */ (0, import_jsx_runtime3.jsxs)("div", { style: { display: "flex", alignItems: "center", gap: "8px", flexWrap: "wrap" }, children: [
          /* @__PURE__ */ (0, import_jsx_runtime3.jsx)(
            "a",
            {
              className: "pg-btn-primary",
              href: mainUrl,
              target: "_blank",
              rel: "noopener noreferrer",
              children: "Inspect on Sponge \u2197"
            }
          ),
          t.secondary_url ? /* @__PURE__ */ (0, import_jsx_runtime3.jsx)(
            "a",
            {
              className: "pg-btn-subtle",
              href: t.secondary_url,
              target: "_blank",
              rel: "noopener noreferrer",
              children: "View Build Logs \u2197"
            }
          ) : null,
          /* @__PURE__ */ (0, import_jsx_runtime3.jsxs)(
            "button",
            {
              type: "button",
              className: "pg-btn-check-rerun",
              id: `btnCheckRerun-${num}`,
              onClick: `openPgChecksRerunModal(${num}, { target: '${safeTargetName}' })`,
              title: `Rerun this check run (${t.name}) (Shortcut: r)`,
              children: [
                /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("span", { children: "\u21BB" }),
                " ",
                /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("span", { children: "Rerun" })
              ]
            }
          ),
          isFailed ? /* @__PURE__ */ (0, import_jsx_runtime3.jsxs)(
            "button",
            {
              type: "button",
              className: "pg-btn-buganizer",
              id: `btnCheckBuganizer-${num}`,
              onClick: `openPgCheckBuganizer(${num}, '${safeTargetName}')`,
              title: "File a new Buganizer issue for this check failure (b/new)",
              children: [
                /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("span", { children: "\u{1F41B}" }),
                " ",
                /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("span", { children: "File Bug" })
              ]
            }
          ) : null,
          /* @__PURE__ */ (0, import_jsx_runtime3.jsxs)(
            "button",
            {
              type: "button",
              className: "pg-btn-subtle",
              id: `btnCheckCopyDiag-${num}`,
              onClick: `copyPgCheckDiagnostics(${num}, '${safeTargetName}')`,
              title: "Copy failure diagnostic report formatted in Markdown",
              children: [
                /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("span", { children: "\u{1F4CB}" }),
                " ",
                /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("span", { children: "Diagnostics" })
              ]
            }
          ),
          /* @__PURE__ */ (0, import_jsx_runtime3.jsxs)(
            "button",
            {
              type: "button",
              className: `pg-btn-subtle ${isLogOpen ? "active" : ""}`,
              id: `btnToggleCheckLog-${num}`,
              onClick: `togglePgCheckLogDrawer(${num}, '${safeTargetName}')`,
              title: "Toggle console run log drawer (l)",
              children: [
                /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("span", { children: "\u{1F4DC}" }),
                " ",
                /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("span", { children: isLogOpen ? "Hide Log" : "Console Log" })
              ]
            }
          )
        ] })
      ] }),
      isFailed ? /* @__PURE__ */ (0, import_jsx_runtime3.jsxs)("div", { className: "pg-checks-callout callout-failed", children: [
        /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("div", { style: { fontWeight: 600, fontSize: "12.5px", color: "#f28b82" }, children: "\u2715 Failure Diagnostics" }),
        /* @__PURE__ */ (0, import_jsx_runtime3.jsx)(
          "div",
          {
            style: {
              fontSize: "12px",
              color: "var(--g-fg)",
              marginTop: "4px",
              wordBreak: "break-word"
            },
            children: t.subtitle || "Target exited with non-zero status or verification failure."
          }
        )
      ] }) : isRunning ? /* @__PURE__ */ (0, import_jsx_runtime3.jsxs)("div", { className: "pg-checks-callout callout-running", children: [
        /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("div", { style: { fontWeight: 600, fontSize: "12.5px", color: "#8ab4f8" }, children: "\u25F7 In Progress" }),
        /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("div", { style: { fontSize: "12px", color: "var(--g-fg)", marginTop: "4px" }, children: t.waiting_on_build ? "Waiting on prerequisite build target to finish." : "Job is currently executing on Android Build worker." })
      ] }) : isPassed ? /* @__PURE__ */ (0, import_jsx_runtime3.jsxs)("div", { className: "pg-checks-callout callout-passed", children: [
        /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("div", { style: { fontWeight: 600, fontSize: "12.5px", color: "#81c995" }, children: "\u2713 Check Passed" }),
        /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("div", { style: { fontSize: "12px", color: "var(--g-fg)", marginTop: "4px" }, children: t.cached ? "Target result verified from Android Build cache." : "Target executed and verified successfully." })
      ] }) : null,
      /* @__PURE__ */ (0, import_jsx_runtime3.jsxs)("div", { className: "pg-checks-props-table", children: [
        /* @__PURE__ */ (0, import_jsx_runtime3.jsxs)("div", { className: "pg-checks-prop-row", children: [
          /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("span", { className: "pg-checks-prop-key", children: "Target Name" }),
          /* @__PURE__ */ (0, import_jsx_runtime3.jsx)(
            "span",
            {
              className: "pg-checks-prop-val",
              style: { fontFamily: "var(--g-mono)", fontSize: "12px" },
              children: t.name
            }
          )
        ] }),
        /* @__PURE__ */ (0, import_jsx_runtime3.jsxs)("div", { className: "pg-checks-prop-row", children: [
          /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("span", { className: "pg-checks-prop-key", children: "Kind" }),
          /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("span", { className: "pg-checks-prop-val", children: kindLabel })
        ] }),
        /* @__PURE__ */ (0, import_jsx_runtime3.jsxs)("div", { className: "pg-checks-prop-row", children: [
          /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("span", { className: "pg-checks-prop-key", children: "Status" }),
          /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("span", { className: "pg-checks-prop-val", children: statusText })
        ] }),
        /* @__PURE__ */ (0, import_jsx_runtime3.jsxs)("div", { className: "pg-checks-prop-row", children: [
          /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("span", { className: "pg-checks-prop-key", children: "Duration" }),
          /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("span", { className: "pg-checks-prop-val", children: durText })
        ] }),
        artifactId ? /* @__PURE__ */ (0, import_jsx_runtime3.jsxs)("div", { className: "pg-checks-prop-row", children: [
          /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("span", { className: "pg-checks-prop-key", children: "Invocation ID" }),
          /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("span", { className: "pg-checks-prop-val", children: /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("code", { style: { fontFamily: "var(--g-mono)", fontSize: "11.5px" }, children: artifactId }) })
        ] }) : null,
        /* @__PURE__ */ (0, import_jsx_runtime3.jsxs)("div", { className: "pg-checks-prop-row", children: [
          /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("span", { className: "pg-checks-prop-key", children: "Blocking Level" }),
          /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("span", { className: "pg-checks-prop-val", children: t.blocking_level || "BLOCKING" })
        ] }),
        /* @__PURE__ */ (0, import_jsx_runtime3.jsxs)("div", { className: "pg-checks-prop-row", children: [
          /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("span", { className: "pg-checks-prop-key", children: "Patchset" }),
          /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("span", { className: "pg-checks-prop-val", children: `PS ${ps}` })
        ] })
      ] }),
      /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("div", { dangerouslySetInnerHTML: { __html: logDrawerHtml } })
    ] });
  }
  function ReplyDialog(props) {
    let {
      num,
      patchSet,
      viewer,
      replyDialogOpen,
      policyNote,
      initialComment,
      hasReplyDraft,
      participantsHtml,
      quickChipsHtml,
      markdownToolbarHtml,
      attentionChipsHtml,
      labelScoresTableHtml
    } = props;
    return /* @__PURE__ */ (0, import_jsx_runtime3.jsx)(
      "div",
      {
        id: "replyDialogBackdrop",
        className: `pg-reply-backdrop${replyDialogOpen ? " open" : ""}`,
        "aria-hidden": replyDialogOpen ? "false" : "true",
        onClick: "cancelReplyDialog()",
        children: /* @__PURE__ */ (0, import_jsx_runtime3.jsxs)(
          "div",
          {
            id: "stagedVoteBar",
            className: "pg-reply-dialog gerrit-vote-panel",
            role: "dialog",
            "aria-modal": "true",
            "aria-label": `Reply to ag/${num}`,
            onClick: "event.stopPropagation()",
            children: [
              /* @__PURE__ */ (0, import_jsx_runtime3.jsxs)("div", { className: "pg-reply-head", children: [
                /* @__PURE__ */ (0, import_jsx_runtime3.jsxs)("span", { children: [
                  "Reply to ",
                  /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("strong", { children: `ag/${num}` }),
                  ` \xB7 PS${patchSet}`
                ] }),
                /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("span", { className: "pg-reply-head-sub", children: `Acting as ${viewer || "viewer"}` })
              ] }),
              /* @__PURE__ */ (0, import_jsx_runtime3.jsx)(
                "div",
                {
                  className: "pg-reply-section pg-reply-participants-section",
                  id: `pgReplyParticipantsSection-${num}`,
                  dangerouslySetInnerHTML: { __html: participantsHtml }
                }
              ),
              /* @__PURE__ */ (0, import_jsx_runtime3.jsxs)("div", { className: "pg-reply-section pg-reply-quick-section", children: [
                /* @__PURE__ */ (0, import_jsx_runtime3.jsxs)("div", { className: "pg-reply-quick-header", children: [
                  /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("span", { className: "pg-reply-quick-title", children: "Quick responses" }),
                  /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("span", { className: "pg-reply-quick-hint", children: "Click preset or press Alt+L" })
                ] }),
                /* @__PURE__ */ (0, import_jsx_runtime3.jsx)(
                  "div",
                  {
                    className: "pg-reply-quick-chips",
                    role: "toolbar",
                    "aria-label": "Quick reply presets",
                    dangerouslySetInnerHTML: { __html: quickChipsHtml }
                  }
                )
              ] }),
              /* @__PURE__ */ (0, import_jsx_runtime3.jsxs)("div", { className: "pg-reply-section pg-reply-message-section", children: [
                /* @__PURE__ */ (0, import_jsx_runtime3.jsxs)("div", { className: "pg-reply-message-head", children: [
                  /* @__PURE__ */ (0, import_jsx_runtime3.jsxs)("div", { className: "pg-reply-message-head-left", children: [
                    /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("span", { className: "pg-reply-message-title", children: "Review Message" }),
                    /* @__PURE__ */ (0, import_jsx_runtime3.jsxs)(
                      "div",
                      {
                        className: "pg-reply-message-tabs",
                        role: "tablist",
                        "aria-label": "Review message editor mode",
                        children: [
                          /* @__PURE__ */ (0, import_jsx_runtime3.jsx)(
                            "button",
                            {
                              type: "button",
                              id: "btnReplyTabEdit",
                              className: "pg-reply-tab is-active",
                              role: "tab",
                              "aria-selected": "true",
                              onClick: `setPgReplyMessageTab('edit', ${num})`,
                              title: "Edit review message (Shortcut: Alt+P)",
                              children: "\u270E Edit"
                            }
                          ),
                          /* @__PURE__ */ (0, import_jsx_runtime3.jsx)(
                            "button",
                            {
                              type: "button",
                              id: "btnReplyTabPreview",
                              className: "pg-reply-tab",
                              role: "tab",
                              "aria-selected": "false",
                              onClick: `setPgReplyMessageTab('preview', ${num})`,
                              title: "Preview formatted markdown message (Shortcut: Alt+P)",
                              children: "\u{1F441} Preview"
                            }
                          )
                        ]
                      }
                    )
                  ] }),
                  /* @__PURE__ */ (0, import_jsx_runtime3.jsxs)("div", { className: "pg-reply-message-actions", children: [
                    /* @__PURE__ */ (0, import_jsx_runtime3.jsx)(
                      "span",
                      {
                        id: "replyDialogAutosaveBadge",
                        className: "pg-draft-autosave-badge is-saved",
                        title: "Draft saved automatically",
                        children: "\u2713 Saved"
                      }
                    ),
                    /* @__PURE__ */ (0, import_jsx_runtime3.jsx)(
                      "button",
                      {
                        type: "button",
                        id: "btnDiscardReplyCommentDraft",
                        className: "pg-btn-subtle pg-btn-discard-reply-draft",
                        style: hasReplyDraft ? void 0 : { display: "none" },
                        onClick: `discardReplyCommentDraft(${num})`,
                        title: "Discard draft review message",
                        children: "Discard"
                      }
                    )
                  ] })
                ] }),
                /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("div", { dangerouslySetInnerHTML: { __html: markdownToolbarHtml } }),
                /* @__PURE__ */ (0, import_jsx_runtime3.jsx)(
                  "textarea",
                  {
                    id: "stagedVoteCommentInput",
                    className: "pg-reply-textarea",
                    rows: 4,
                    placeholder: "Say something nice\u2026 (Ctrl+Enter to send \xB7 Esc to close & keep draft \xB7 Alt+L for LGTM +2 \xB7 Markdown supported)",
                    onInput: "onStagedVoteCommentInput(this.value)",
                    onBlur: `flushPgReplyDialogDraftAutosave(${num})`,
                    onKeyDown: "onStagedVoteCommentKeydown(event)",
                    defaultValue: initialComment
                  }
                ),
                /* @__PURE__ */ (0, import_jsx_runtime3.jsx)(
                  "div",
                  {
                    id: "pgReplyMessagePreview",
                    className: "pg-reply-message-preview",
                    style: { display: "none" },
                    "aria-label": "Review message markdown preview"
                  }
                )
              ] }),
              /* @__PURE__ */ (0, import_jsx_runtime3.jsxs)(
                "div",
                {
                  id: `pgReplyDraftsSection-${num}`,
                  className: "pg-reply-drafts-section",
                  style: { display: "none" },
                  children: [
                    /* @__PURE__ */ (0, import_jsx_runtime3.jsxs)("div", { className: "pg-reply-drafts-head", children: [
                      /* @__PURE__ */ (0, import_jsx_runtime3.jsxs)("div", { className: "pg-reply-drafts-head-left", children: [
                        /* @__PURE__ */ (0, import_jsx_runtime3.jsxs)("span", { className: "pg-reply-drafts-title", children: [
                          "Draft Comments (",
                          /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("span", { id: `pgReplyDraftsCount-${num}`, children: "0" }),
                          ")"
                        ] }),
                        /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("span", { className: "pg-reply-drafts-sub", children: "Will be published to Gerrit on Send" })
                      ] }),
                      /* @__PURE__ */ (0, import_jsx_runtime3.jsx)(
                        "button",
                        {
                          id: `btnDiscardAllDrafts-${num}`,
                          type: "button",
                          className: "pg-reply-draft-discard pg-btn-discard-all-drafts",
                          onClick: `discardAllReplyDialogDrafts(${num})`,
                          title: "Discard all draft comments on this change",
                          children: "\u2715 Discard all drafts"
                        }
                      )
                    ] }),
                    /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("div", { id: `pgReplyDraftsList-${num}`, className: "pg-reply-drafts-list" })
                  ]
                }
              ),
              /* @__PURE__ */ (0, import_jsx_runtime3.jsxs)("div", { className: "pg-reply-section pg-reply-attention-section", children: [
                /* @__PURE__ */ (0, import_jsx_runtime3.jsxs)("div", { className: "pg-reply-attention-head", children: [
                  /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("span", { className: "pg-reply-attention-title", children: "Attention Set" }),
                  /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("span", { className: "pg-reply-attention-sub", children: "Select who receives attention upon sending this reply" })
                ] }),
                /* @__PURE__ */ (0, import_jsx_runtime3.jsx)(
                  "div",
                  {
                    className: "pg-reply-attention-chips",
                    id: `pgReplyAttentionChips-${num}`,
                    dangerouslySetInnerHTML: { __html: attentionChipsHtml }
                  }
                ),
                /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("div", { className: "pg-reply-attention-reason-row", children: /* @__PURE__ */ (0, import_jsx_runtime3.jsx)(
                  "input",
                  {
                    type: "text",
                    id: "pgReplyAttentionReasonInput",
                    className: "pg-reply-attention-reason-input",
                    placeholder: "Reason for attention set update (optional)",
                    defaultValue: ""
                  }
                ) })
              ] }),
              /* @__PURE__ */ (0, import_jsx_runtime3.jsx)(
                "div",
                {
                  className: "pg-reply-section",
                  dangerouslySetInnerHTML: { __html: labelScoresTableHtml }
                }
              ),
              /* @__PURE__ */ (0, import_jsx_runtime3.jsxs)("div", { className: "pg-reply-footer", children: [
                /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("span", { className: "pg-reply-attn-note", children: policyNote }),
                /* @__PURE__ */ (0, import_jsx_runtime3.jsxs)("div", { className: "pg-reply-footer-btns", children: [
                  /* @__PURE__ */ (0, import_jsx_runtime3.jsx)(
                    "button",
                    {
                      id: "btnDiscardAllDraftsFooter",
                      type: "button",
                      className: "pg-btn-flat pg-btn-discard-drafts",
                      style: { display: "none" },
                      onClick: "discardAllReplyDialogDrafts(state.selectedClNumber)",
                      title: "Discard all draft comments on this change",
                      children: "Discard All Drafts"
                    }
                  ),
                  /* @__PURE__ */ (0, import_jsx_runtime3.jsx)(
                    "button",
                    {
                      id: "btnResetStagedVotes",
                      type: "button",
                      className: "pg-btn-flat",
                      style: { display: "none" },
                      onClick: "resetStagedVote()",
                      title: "Reset staged scores",
                      children: "Reset"
                    }
                  ),
                  /* @__PURE__ */ (0, import_jsx_runtime3.jsx)(
                    "button",
                    {
                      id: "btnCancelReplyDialog",
                      type: "button",
                      className: "pg-btn-flat",
                      onClick: "cancelReplyDialog()",
                      title: "Cancel and close reply dialog (Esc)",
                      children: "Cancel"
                    }
                  ),
                  /* @__PURE__ */ (0, import_jsx_runtime3.jsx)(
                    "button",
                    {
                      id: "btnSubmitVotes",
                      type: "button",
                      className: "pg-btn-primary btn-submit-votes",
                      onClick: "submitStagedVotes(this)",
                      disabled: !0,
                      title: "Send reply and votes to Gerrit (Enter / Ctrl+Enter)",
                      children: "Send"
                    }
                  )
                ] })
              ] })
            ]
          }
        )
      }
    );
  }
  function ChecksView(props) {
    let {
      num,
      ps,
      isLoading,
      errorMsg,
      checksUrl,
      workplanUrl,
      workplanId,
      isEmptyTargets,
      viewerPresubmitReady,
      bannerStatusClass,
      bannerIcon,
      bannerTitle,
      liveTargetsSummaryText,
      failWarnCnt,
      activeCnt,
      totalCnt,
      passedCnt,
      filterId,
      kindFilter,
      kindCounts,
      rawSearchQuery,
      filteredTargets,
      selectedTargetName,
      detailHtml
    } = props;
    if (isLoading)
      return /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("div", { className: "pg-checks-view", children: /* @__PURE__ */ (0, import_jsx_runtime3.jsxs)("div", { className: "pg-checks-loading", children: [
        /* @__PURE__ */ (0, import_jsx_runtime3.jsx)(
          "span",
          {
            className: "btn-spinner",
            style: { width: "20px", height: "20px", borderWidth: "2.5px" }
          }
        ),
        /* @__PURE__ */ (0, import_jsx_runtime3.jsxs)("div", { style: { display: "flex", flexDirection: "column", gap: "4px" }, children: [
          /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("span", { style: { fontWeight: 600, fontSize: "13px", color: "var(--g-fg)" }, children: `Querying TreeHugger presubmit runs for ag/${num} (Patchset ${ps})\u2026` }),
          /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("span", { style: { fontSize: "11.5px", color: "var(--g-muted)" }, children: "Fetching build and test targets from Android Build" })
        ] })
      ] }) });
    if (errorMsg)
      return /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("div", { className: "pg-checks-view", children: /* @__PURE__ */ (0, import_jsx_runtime3.jsxs)("div", { className: "pg-checks-error", children: [
        /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("div", { style: { fontWeight: 600, fontSize: "13px", color: "#f28b82" }, children: "Failed to load TreeHugger presubmit checks" }),
        /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("div", { style: { fontSize: "12px", color: "var(--g-muted)" }, children: errorMsg }),
        /* @__PURE__ */ (0, import_jsx_runtime3.jsxs)("div", { style: { display: "flex", alignItems: "center", gap: "10px", marginTop: "8px" }, children: [
          /* @__PURE__ */ (0, import_jsx_runtime3.jsx)(
            "button",
            {
              type: "button",
              className: "pg-btn-primary",
              onClick: `event.stopPropagation(); fetchPresubmitTargetsForCl(${num}, { forceRefresh: true })`,
              children: "\u21BB Retry"
            }
          ),
          checksUrl ? /* @__PURE__ */ (0, import_jsx_runtime3.jsx)(
            "a",
            {
              className: "ag-link",
              href: checksUrl,
              target: "_blank",
              rel: "noopener noreferrer",
              children: "Open Gerrit Checks \u2197"
            }
          ) : null
        ] })
      ] }) });
    if (isEmptyTargets)
      return /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("div", { className: "pg-checks-view", children: /* @__PURE__ */ (0, import_jsx_runtime3.jsxs)("div", { className: "pg-checks-empty-card", children: [
        /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("div", { style: { fontSize: "28px" }, children: "\u23F3" }),
        /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("div", { style: { fontWeight: 600, fontSize: "14px", color: "var(--g-fg)" }, children: `No TreeHugger checks recorded yet for ag/${num} (Patchset ${ps})` }),
        /* @__PURE__ */ (0, import_jsx_runtime3.jsx)(
          "div",
          {
            style: {
              fontSize: "12px",
              color: "var(--g-muted)",
              maxWidth: "500px",
              textAlign: "center"
            },
            children: "TreeHugger runs automatically on Android Build once Presubmit-Ready +1 is cast by an authorized reviewer."
          }
        ),
        /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("div", { style: { marginTop: "8px" }, children: viewerPresubmitReady ? /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("span", { style: { color: "#fdd663", fontWeight: 600, fontSize: "12px" }, children: "\u23F3 TreeHugger workplan is allocating build & test targets\u2026" }) : /* @__PURE__ */ (0, import_jsx_runtime3.jsx)(
          "button",
          {
            type: "button",
            className: "pg-btn-primary",
            onClick: "event.stopPropagation(); toggleStagedPr(true)",
            children: "\u2611 Stage Presubmit-Ready (PR +1)"
          }
        ) })
      ] }) });
    let searchQueryLower = String(rawSearchQuery || "").trim().toLowerCase();
    return /* @__PURE__ */ (0, import_jsx_runtime3.jsxs)("div", { className: "pg-checks-view", children: [
      /* @__PURE__ */ (0, import_jsx_runtime3.jsxs)("div", { className: `pg-checks-banner ${bannerStatusClass}`, children: [
        /* @__PURE__ */ (0, import_jsx_runtime3.jsxs)("div", { className: "pg-checks-banner-left", children: [
          /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("span", { className: "pg-checks-banner-icon", children: bannerIcon }),
          /* @__PURE__ */ (0, import_jsx_runtime3.jsxs)("div", { style: { display: "flex", flexDirection: "column", gap: "2px" }, children: [
            /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("span", { className: "pg-checks-banner-title", children: bannerTitle }),
            /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("span", { className: "pg-checks-banner-sub", children: `Patchset ${ps} \xB7 ${liveTargetsSummaryText}${workplanId ? ` \xB7 Workplan ${workplanId}` : ""}` })
          ] })
        ] }),
        /* @__PURE__ */ (0, import_jsx_runtime3.jsxs)("div", { className: "pg-checks-banner-right", children: [
          workplanUrl ? /* @__PURE__ */ (0, import_jsx_runtime3.jsx)(
            "a",
            {
              className: "ag-link",
              style: { fontSize: "12px" },
              href: workplanUrl,
              target: "_blank",
              rel: "noopener noreferrer",
              children: "Workplan \u2197"
            }
          ) : null,
          checksUrl ? /* @__PURE__ */ (0, import_jsx_runtime3.jsx)(
            "a",
            {
              className: "ag-link",
              style: { fontSize: "12px" },
              href: checksUrl,
              target: "_blank",
              rel: "noopener noreferrer",
              children: "Gerrit Checks \u2197"
            }
          ) : null,
          failWarnCnt > 0 ? /* @__PURE__ */ (0, import_jsx_runtime3.jsx)(
            "button",
            {
              type: "button",
              className: "pg-btn-rerun-failed",
              onClick: `openPgChecksRerunModal(${num}, { mode: 'failed' })`,
              title: "Rerun all failed presubmit checks",
              children: `\u21BB Rerun Failed (${failWarnCnt})`
            }
          ) : null,
          /* @__PURE__ */ (0, import_jsx_runtime3.jsx)(
            "button",
            {
              type: "button",
              className: "pg-btn-subtle",
              onClick: `openPgChecksRerunModal(${num}, { mode: 'all' })`,
              title: "Rerun presubmit checks (Shortcut: r)",
              children: "\u21BB Rerun\u2026"
            }
          ),
          /* @__PURE__ */ (0, import_jsx_runtime3.jsx)(
            "button",
            {
              type: "button",
              className: "pg-btn-subtle",
              onClick: `fetchPresubmitTargetsForCl(${num}, { forceRefresh: true })`,
              title: "Refresh runs",
              children: "\u21BB Refresh"
            }
          )
        ] })
      ] }),
      /* @__PURE__ */ (0, import_jsx_runtime3.jsxs)("div", { className: "pg-checks-filter-bar", children: [
        /* @__PURE__ */ (0, import_jsx_runtime3.jsxs)("div", { className: "pg-checks-filter-tabs", children: [
          /* @__PURE__ */ (0, import_jsx_runtime3.jsx)(
            "button",
            {
              type: "button",
              className: `pg-checks-filter-tab ${filterId === "all" ? "active" : ""}`,
              onClick: `setPgChecksCategoryFilter(${num}, 'all')`,
              children: `All (${totalCnt})`
            }
          ),
          /* @__PURE__ */ (0, import_jsx_runtime3.jsx)(
            "button",
            {
              type: "button",
              className: `pg-checks-filter-tab ${filterId === "failed" ? "active" : ""}`,
              onClick: `setPgChecksCategoryFilter(${num}, 'failed')`,
              children: `Failed (${failWarnCnt})`
            }
          ),
          /* @__PURE__ */ (0, import_jsx_runtime3.jsx)(
            "button",
            {
              type: "button",
              className: `pg-checks-filter-tab ${filterId === "running" ? "active" : ""}`,
              onClick: `setPgChecksCategoryFilter(${num}, 'running')`,
              children: `Running (${activeCnt})`
            }
          ),
          /* @__PURE__ */ (0, import_jsx_runtime3.jsx)(
            "button",
            {
              type: "button",
              className: `pg-checks-filter-tab ${filterId === "passed" ? "active" : ""}`,
              onClick: `setPgChecksCategoryFilter(${num}, 'passed')`,
              children: `Passed (${passedCnt})`
            }
          )
        ] }),
        /* @__PURE__ */ (0, import_jsx_runtime3.jsxs)("div", { className: "pg-checks-search-wrap", children: [
          /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("span", { className: "pg-checks-search-icon", children: "\u{1F50D}" }),
          /* @__PURE__ */ (0, import_jsx_runtime3.jsx)(
            "input",
            {
              id: `pgChecksSearchInput-${num}`,
              type: "text",
              className: "pg-checks-search-input",
              value: rawSearchQuery || "",
              onInput: `onPgChecksSearchInput(${num}, this.value)`,
              placeholder: "Filter checks (e.g. goldfish, test, build)...",
              "aria-label": "Filter checks"
            }
          ),
          searchQueryLower ? /* @__PURE__ */ (0, import_jsx_runtime3.jsx)(
            "button",
            {
              type: "button",
              className: "pg-checks-search-clear",
              onClick: `clearPgChecksSearch(${num})`,
              title: "Clear filter",
              children: "\u2715"
            }
          ) : null
        ] })
      ] }),
      /* @__PURE__ */ (0, import_jsx_runtime3.jsxs)("div", { className: "pg-checks-kind-tabs", id: `pgChecksKindTabs-${num}`, children: [
        /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("span", { className: "pg-checks-kind-label", children: "Kind:" }),
        /* @__PURE__ */ (0, import_jsx_runtime3.jsx)(
          "button",
          {
            type: "button",
            className: `pg-checks-kind-tab ${kindFilter === "all" ? "active" : ""}`,
            onClick: `setPgChecksKindFilter(${num}, 'all')`,
            children: `All (${kindCounts.all})`
          }
        ),
        /* @__PURE__ */ (0, import_jsx_runtime3.jsx)(
          "button",
          {
            type: "button",
            className: `pg-checks-kind-tab ${kindFilter === "build" ? "active" : ""}`,
            onClick: `setPgChecksKindFilter(${num}, 'build')`,
            children: `\u{1F3D7}\uFE0F Build (${kindCounts.build})`
          }
        ),
        /* @__PURE__ */ (0, import_jsx_runtime3.jsx)(
          "button",
          {
            type: "button",
            className: `pg-checks-kind-tab ${kindFilter === "test" ? "active" : ""}`,
            onClick: `setPgChecksKindFilter(${num}, 'test')`,
            children: `\u{1F9EA} Test (${kindCounts.test})`
          }
        ),
        /* @__PURE__ */ (0, import_jsx_runtime3.jsx)(
          "button",
          {
            type: "button",
            className: `pg-checks-kind-tab ${kindFilter === "preflight" ? "active" : ""}`,
            onClick: `setPgChecksKindFilter(${num}, 'preflight')`,
            children: `\u26A1 Preflight (${kindCounts.preflight})`
          }
        )
      ] }),
      filteredTargets.length === 0 ? /* @__PURE__ */ (0, import_jsx_runtime3.jsxs)("div", { className: "pg-checks-empty-filtered", children: [
        /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("span", { children: `No checks match "${searchQueryLower || filterId}".` }),
        /* @__PURE__ */ (0, import_jsx_runtime3.jsx)(
          "button",
          {
            type: "button",
            className: "pg-btn-subtle",
            style: { marginLeft: "8px" },
            onClick: `clearPgChecksSearch(${num})`,
            children: "Clear Filter"
          }
        )
      ] }) : /* @__PURE__ */ (0, import_jsx_runtime3.jsxs)("div", { className: "pg-checks-grid", children: [
        /* @__PURE__ */ (0, import_jsx_runtime3.jsxs)("div", { className: "pg-checks-runs-col", children: [
          /* @__PURE__ */ (0, import_jsx_runtime3.jsxs)("div", { className: "pg-checks-runs-header", children: [
            /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("span", { children: `Runs (${filteredTargets.length})` }),
            /* @__PURE__ */ (0, import_jsx_runtime3.jsxs)("div", { className: "pg-checks-runs-header-actions", children: [
              failWarnCnt > 0 ? /* @__PURE__ */ (0, import_jsx_runtime3.jsx)(
                "button",
                {
                  type: "button",
                  className: "pg-btn-subtle-rerun",
                  id: `btnChecksRerunFailed-${num}`,
                  onClick: `openPgChecksRerunModal(${num}, { mode: 'failed' })`,
                  title: "Rerun failed checks",
                  children: `\u21BB Failed (${failWarnCnt})`
                }
              ) : null,
              /* @__PURE__ */ (0, import_jsx_runtime3.jsx)(
                "button",
                {
                  type: "button",
                  className: "pg-btn-subtle",
                  id: `btnChecksRerunAll-${num}`,
                  onClick: `openPgChecksRerunModal(${num})`,
                  title: "Rerun checks menu (Shortcut: r)",
                  children: "\u21BB Rerun"
                }
              )
            ] })
          ] }),
          /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("div", { className: "pg-checks-runs-list", children: filteredTargets.map((t, idx) => {
            let isSel = !!(selectedTargetName && selectedTargetName === t.name), kindLabel = t.kind && PRESUBMIT_KIND_LABELS[t.kind] || "TARGET", durText = t.duration_display || "", safeName = escapeJsQuote(t.name || "");
            return /* @__PURE__ */ (0, import_jsx_runtime3.jsxs)(
              "div",
              {
                className: `pg-checks-run-item ${isSel ? "is-selected" : ""}`,
                onClick: `selectPgCheckRunByIndex(${num}, ${idx})`,
                children: [
                  /* @__PURE__ */ (0, import_jsx_runtime3.jsxs)("div", { className: "pg-checks-run-item-left", children: [
                    /* @__PURE__ */ (0, import_jsx_runtime3.jsx)(PresubmitStatusPill, { t }),
                    /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("span", { className: "pg-checks-kind-badge", children: kindLabel }),
                    /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("span", { className: "pg-checks-run-name", title: t.name, children: t.name })
                  ] }),
                  /* @__PURE__ */ (0, import_jsx_runtime3.jsxs)("div", { className: "pg-checks-run-item-right", children: [
                    durText ? /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("span", { className: "pg-checks-run-dur", children: durText }) : null,
                    /* @__PURE__ */ (0, import_jsx_runtime3.jsx)(
                      "button",
                      {
                        type: "button",
                        className: "pg-btn-run-rerun",
                        onClick: `event.stopPropagation(); triggerPgSingleCheckRerun(${num}, '${safeName}')`,
                        title: `Rerun ${t.name}`,
                        children: "\u21BB"
                      }
                    )
                  ] })
                ]
              },
              idx
            );
          }) })
        ] }),
        /* @__PURE__ */ (0, import_jsx_runtime3.jsx)(
          "div",
          {
            className: "pg-checks-detail-col",
            dangerouslySetInnerHTML: { __html: detailHtml }
          }
        )
      ] })
    ] });
  }
  function SavedSearchesList(props) {
    let { items } = props;
    return !items || items.length === 0 ? /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("div", { style: { padding: "24px", textAlign: "center", color: "var(--g-muted)" }, children: "No saved searches or custom dashboard sections configured. Use the form above to add one, or click Reset Defaults." }) : /* @__PURE__ */ (0, import_jsx_runtime3.jsx)(import_jsx_runtime3.Fragment, { children: items.map((s, idx) => {
      let safeId = escapeJsQuote(s.id);
      return /* @__PURE__ */ (0, import_jsx_runtime3.jsxs)(
        "div",
        {
          className: `pg-saved-search-card ${s.showOnDashboard ? "is-active-on-dash" : ""}`,
          id: `pgSavedSearchCard-${s.id}`,
          children: [
            /* @__PURE__ */ (0, import_jsx_runtime3.jsxs)("div", { className: "pg-saved-search-card-left", children: [
              /* @__PURE__ */ (0, import_jsx_runtime3.jsxs)("div", { className: "pg-saved-search-reorder-btns", children: [
                /* @__PURE__ */ (0, import_jsx_runtime3.jsx)(
                  "button",
                  {
                    type: "button",
                    className: "pg-btn-reorder",
                    onClick: `movePgSavedSearch('${safeId}', -1)`,
                    title: "Move up",
                    disabled: idx === 0,
                    children: "\u25B2"
                  }
                ),
                /* @__PURE__ */ (0, import_jsx_runtime3.jsx)(
                  "button",
                  {
                    type: "button",
                    className: "pg-btn-reorder",
                    onClick: `movePgSavedSearch('${safeId}', 1)`,
                    title: "Move down",
                    disabled: idx === items.length - 1,
                    children: "\u25BC"
                  }
                )
              ] }),
              /* @__PURE__ */ (0, import_jsx_runtime3.jsxs)("div", { className: "pg-saved-search-info", children: [
                /* @__PURE__ */ (0, import_jsx_runtime3.jsxs)("div", { className: "pg-saved-search-title-row", children: [
                  /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("span", { className: "pg-saved-search-name", children: s.title }),
                  s.matchCount > 0 ? /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("span", { className: "pg-saved-search-match-badge has-matches", children: `${s.matchCount} match${s.matchCount === 1 ? "" : "es"}` }) : /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("span", { className: "pg-saved-search-match-badge", children: "0 matches" })
                ] }),
                /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("div", { className: "pg-saved-search-query-text", title: s.query, children: s.query })
              ] })
            ] }),
            /* @__PURE__ */ (0, import_jsx_runtime3.jsxs)("div", { className: "pg-saved-search-card-right", children: [
              /* @__PURE__ */ (0, import_jsx_runtime3.jsxs)(
                "label",
                {
                  className: "pg-saved-search-check-label",
                  title: "Toggle whether this section appears on your PolyGerrit dashboard",
                  children: [
                    /* @__PURE__ */ (0, import_jsx_runtime3.jsx)(
                      "input",
                      {
                        type: "checkbox",
                        checked: !!s.showOnDashboard,
                        onChange: `toggleSavedSearchDashboardVisibility('${safeId}')`
                      }
                    ),
                    /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("span", { children: "Dashboard" })
                  ]
                }
              ),
              /* @__PURE__ */ (0, import_jsx_runtime3.jsx)(
                "button",
                {
                  type: "button",
                  className: "btn pg-btn-flat",
                  onClick: `applyPgSavedSearch('${safeId}')`,
                  title: "Run query in search bar",
                  children: "\u25B6 Run"
                }
              ),
              /* @__PURE__ */ (0, import_jsx_runtime3.jsx)(
                "button",
                {
                  type: "button",
                  className: "btn pg-btn-flat",
                  onClick: `deletePgSavedSearch('${safeId}')`,
                  title: "Delete this saved search",
                  children: "\u{1F5D1}"
                }
              )
            ] })
          ]
        },
        s.id
      );
    }) });
  }
  function filterPresubmitTargetsByCategory(targets, filterId) {
    return !filterId || filterId === "all" ? targets : filterId === "failed" ? targets.filter((t) => t.status === "failed" || t.status === "warning") : filterId === "running" ? targets.filter((t) => t.status === "running" || t.status === "queued") : filterId === "passed" ? targets.filter((t) => t.status === "passed") : targets;
  }
  function renderPresubmitStatusPillHtml(t) {
    return renderReactToHtml(/* @__PURE__ */ (0, import_jsx_runtime3.jsx)(PresubmitStatusPill, { t: t || { name: "" } }));
  }
  function renderSinglePresubmitTargetRowHtml(t, fallbackChecksUrl) {
    return renderReactToHtml(
      /* @__PURE__ */ (0, import_jsx_runtime3.jsx)(
        SinglePresubmitTargetRow,
        {
          t: t || { name: "" },
          fallbackChecksUrl
        }
      )
    );
  }
  function renderPgChecksTabButtonHtml(c, activeTab, entry, pv) {
    let num = Number(c && c.number ? c.number : 0), isActive = activeTab === "checks", counts = entry?.data?.counts || null, badgeClass = "", badgeText = "";
    return counts && (counts.total || 0) > 0 ? (counts.failed || 0) > 0 ? (badgeClass = "st-failed", badgeText = `\u2715 ${counts.failed}`) : (counts.warning || 0) > 0 ? (badgeClass = "st-warning", badgeText = `\u26A0 ${counts.warning}`) : (counts.running || 0) > 0 || (counts.queued || 0) > 0 ? (badgeClass = "st-running", badgeText = `\u25F7 ${(counts.running || 0) + (counts.queued || 0)}`) : (counts.passed || 0) > 0 ? (badgeClass = "st-passed", badgeText = `\u2713 ${counts.passed}`) : badgeText = String(counts.total) : pv?.status === "failed" ? (badgeClass = "st-failed", badgeText = "\u2715 Fail") : pv?.status === "warning" ? (badgeClass = "st-warning", badgeText = "\u26A0 Warn") : pv?.status === "running" || pv?.status === "queued" ? (badgeClass = "st-running", badgeText = "\u25F7 Run") : pv?.status === "passed" && (badgeClass = "st-passed", badgeText = "\u2713 Pass"), renderReactToHtml(
      /* @__PURE__ */ (0, import_jsx_runtime3.jsx)(
        ChecksTabButton,
        {
          num,
          isActive,
          badgeClass,
          badgeText
        }
      )
    );
  }
  function renderPgChecksControlsHtml(c, entry, pv) {
    let num = Number(c && c.number ? c.number : 0), isRefreshing = !!(entry && entry.loading), showStagePrBtn = !c?.viewer_presubmit_ready && pv?.status !== "passed";
    return renderReactToHtml(
      /* @__PURE__ */ (0, import_jsx_runtime3.jsx)(
        ChecksControls,
        {
          num,
          isRefreshing,
          showStagePrBtn,
          workplanUrl: pv?.workplanUrl,
          workplanId: pv?.workplanId,
          checksUrl: pv?.checksUrl
        }
      )
    );
  }
  function renderPgCheckRunLogDrawerHtml(c, t, _pv, isLogOpen, rawLogSearch, allLines) {
    let num = Number(c.number || 0), logSearchLower = String(rawLogSearch || "").trim().toLowerCase(), visibleLines = logSearchLower ? (allLines || []).filter((l) => l.text.toLowerCase().includes(logSearchLower)) : allLines || [];
    return renderReactToHtml(
      /* @__PURE__ */ (0, import_jsx_runtime3.jsx)(
        CheckRunLogDrawer,
        {
          num,
          target: t,
          isLogOpen,
          rawLogSearch: rawLogSearch || "",
          visibleLines
        }
      )
    );
  }
  function renderPgCheckDetailCardHtml(c, t, pv, isLogOpen, rawLogSearch, allLines) {
    let num = Number(c.number || 0), ps = Number(c.patch_set || 1), logDrawerHtml = renderPgCheckRunLogDrawerHtml(
      c,
      t,
      pv,
      isLogOpen,
      rawLogSearch,
      allLines
    );
    return renderReactToHtml(
      /* @__PURE__ */ (0, import_jsx_runtime3.jsx)(
        CheckDetailCard,
        {
          num,
          ps,
          target: t,
          checksUrl: pv?.checksUrl,
          isLogOpen,
          logDrawerHtml
        }
      )
    );
  }
  function renderPgChecksViewComponentHtml(c, entry, pv, filterId, kindFilter, rawSearchQuery, selectedRunName, isLogOpen, rawLogSearch, liveTargetsSummaryText, getLogLines) {
    let num = Number(c.number || 0), ps = Number(c.patch_set || 1), tData = entry?.data || null, counts = tData?.counts || {
      total: 0,
      failed: 0,
      warning: 0,
      running: 0,
      queued: 0,
      passed: 0,
      cached: 0
    }, targets = tData?.targets || [], isLoading = !entry || !!(entry.loading && !entry.data), errorMsg = entry && entry.error && !entry.data ? entry.error : void 0, isEmptyTargets = !isLoading && !errorMsg && targets.length === 0, failWarnCnt = (counts.failed || 0) + (counts.warning || 0), activeCnt = (counts.running || 0) + (counts.queued || 0), totalCnt = counts.total || 0, passedCnt = counts.passed || 0, kindCounts = {
      all: targets.length,
      build: targets.filter((t) => String(t.kind || "").toLowerCase().includes("build")).length,
      test: targets.filter((t) => String(t.kind || "").toLowerCase().includes("test")).length,
      preflight: targets.filter((t) => {
        let k = String(t.kind || "").toLowerCase();
        return k.includes("preflight") || k.includes("check") || k.includes("lint");
      }).length
    }, filtered = filterPresubmitTargetsByCategory(targets, filterId || "all");
    kindFilter && kindFilter !== "all" && (filtered = filtered.filter((t) => {
      let k = String(t.kind || "").toLowerCase();
      return kindFilter === "build" ? k.includes("build") : kindFilter === "test" ? k.includes("test") : kindFilter === "preflight" ? k.includes("preflight") || k.includes("check") || k.includes("lint") : !0;
    }));
    let searchQuery = String(rawSearchQuery || "").trim().toLowerCase();
    searchQuery && (filtered = filtered.filter((t) => {
      let name = String(t.name || "").toLowerCase(), sub = String(t.subtitle || "").toLowerCase(), kind = String(t.kind || "").toLowerCase(), art = String(t.artifact_id || "").toLowerCase();
      return name.includes(searchQuery) || sub.includes(searchQuery) || kind.includes(searchQuery) || art.includes(searchQuery);
    }));
    let selectedTarget = null;
    selectedRunName && (selectedTarget = filtered.find((t) => t.name === selectedRunName) || null), !selectedTarget && filtered.length > 0 && (selectedTarget = filtered.find((t) => t.status === "failed" || t.status === "warning") || filtered.find((t) => t.status === "running" || t.status === "queued") || filtered[0]);
    let bannerStatusClass = "banner-passed", bannerIcon = "\u2713", bannerTitle = `All ${targets.length} TreeHugger checks passed`;
    (counts.failed || 0) > 0 ? (bannerStatusClass = "banner-failed", bannerIcon = "\u2715", bannerTitle = `${counts.failed} of ${targets.length} checks failed`) : (counts.warning || 0) > 0 ? (bannerStatusClass = "banner-warning", bannerIcon = "\u26A0", bannerTitle = `${counts.warning} checks with warnings`) : activeCnt > 0 && (bannerStatusClass = "banner-running", bannerIcon = "\u25F7", bannerTitle = `${activeCnt} checks running (${passedCnt} passed)`);
    let detailHtml = selectedTarget ? renderPgCheckDetailCardHtml(
      c,
      selectedTarget,
      pv,
      isLogOpen,
      rawLogSearch,
      getLogLines ? getLogLines(selectedTarget) : []
    ) : '<div style="padding:24px;color:var(--g-muted);font-size:12px;">Select a check run on the left to inspect its details and logs.</div>';
    return renderReactToHtml(
      /* @__PURE__ */ (0, import_jsx_runtime3.jsx)(
        ChecksView,
        {
          num,
          ps,
          isLoading,
          errorMsg,
          checksUrl: pv?.checksUrl,
          workplanUrl: pv?.workplanUrl,
          workplanId: pv?.workplanId,
          isEmptyTargets,
          viewerPresubmitReady: !!c.viewer_presubmit_ready,
          bannerStatusClass,
          bannerIcon,
          bannerTitle,
          liveTargetsSummaryText,
          failWarnCnt,
          activeCnt,
          totalCnt,
          passedCnt,
          filterId: filterId || "all",
          kindFilter: kindFilter || "all",
          kindCounts,
          rawSearchQuery: rawSearchQuery || "",
          filteredTargets: filtered,
          selectedTargetName: selectedTarget?.name,
          detailHtml
        }
      )
    );
  }
  function renderPgSavedSearchesCardsHtml(items) {
    return renderReactToHtml(/* @__PURE__ */ (0, import_jsx_runtime3.jsx)(SavedSearchesList, { items: items || [] }));
  }
  function renderPgReplyDialogComponentHtml(c, replyDialogOpen, viewer, policyNote, initialComment, hasReplyDraft, participantsHtml, quickChipsHtml, markdownToolbarHtml, attentionChipsHtml, labelScoresTableHtml) {
    return renderReactToHtml(
      /* @__PURE__ */ (0, import_jsx_runtime3.jsx)(
        ReplyDialog,
        {
          num: Number(c.number || 0),
          patchSet: Number(c.patch_set || 1),
          viewer: viewer || "viewer",
          replyDialogOpen,
          policyNote,
          initialComment,
          hasReplyDraft,
          participantsHtml,
          quickChipsHtml,
          markdownToolbarHtml,
          attentionChipsHtml,
          labelScoresTableHtml
        }
      )
    );
  }

  // ../../../../../../../../execroot/_main/bazel-out/k8-fastbuild-ST-f481a530da89/bin/external/aemu+/tools/emu-dev-cli/src/ui/gerrit/components/CodeViewerTreeSidebar.tsx
  var import_jsx_runtime4 = __toESM(require_jsx_runtime()), COMMIT_MSG_MAGIC_PATH = "/COMMIT_MSG", MERGE_LIST_MAGIC_PATH = "/MERGE_LIST", TREE_INDENT_BASE_PX = 6, TREE_INDENT_STEP_PX = 14, BTN_CODE_VIEWER_PREFIX = "btnCodeViewerMode-";
  function createMutableDirNode(segment, dirPath) {
    return {
      segment,
      dirPath,
      childMap: /* @__PURE__ */ new Map(),
      files: []
    };
  }
  function insertFileIntoMutableTree(root, entry) {
    let parts = String(entry.file || "").split("/").filter(Boolean);
    if (parts.length <= 1) {
      root.files.push(entry);
      return;
    }
    let curr = root, accPath = "";
    for (let i = 0; i < parts.length - 1; i++) {
      let seg = parts[i];
      accPath = accPath ? `${accPath}/${seg}` : seg;
      let next = curr.childMap.get(seg);
      next || (next = createMutableDirNode(seg, accPath), curr.childMap.set(seg, next)), curr = next;
    }
    curr.files.push(entry);
  }
  function compareCaseInsensitive(a, b) {
    let al = a.toLowerCase(), bl = b.toLowerCase();
    return al !== bl ? al < bl ? -1 : 1 : a < b ? -1 : a > b ? 1 : 0;
  }
  function compactSingleChildChain(node) {
    let curr = node, displayName = node.segment;
    for (; curr.files.length === 0 && curr.childMap.size === 1; ) {
      let onlyChild = curr.childMap.values().next().value;
      displayName = `${displayName}/${onlyChild.segment}`, curr = onlyChild;
    }
    return { target: curr, displayName };
  }
  function finalizeDirNode(rawNode, isRoot) {
    let compacted = isRoot ? { target: rawNode, displayName: "" } : compactSingleChildChain(rawNode), target = compacted.target, children = Array.from(target.childMap.values()).sort(
      (a, b) => compareCaseInsensitive(a.segment, b.segment)
    ).map((child) => finalizeDirNode(child, !1)), files = [...target.files].sort(
      (a, b) => compareCaseInsensitive(getBasename(a.file), getBasename(b.file))
    ), totalFileCount = files.length, reviewedFileCount = files.filter((f) => !!f.isReviewed).length, unresolvedCount = files.reduce((acc, f) => acc + Number(f.unresolvedCount || 0), 0), draftCount = files.reduce((acc, f) => acc + Number(f.draftCount || 0), 0), additions = files.reduce((acc, f) => acc + Number(f.additions || 0), 0), deletions = files.reduce((acc, f) => acc + Number(f.deletions || 0), 0);
    for (let child of children)
      totalFileCount += child.totalFileCount, reviewedFileCount += child.reviewedFileCount, unresolvedCount += child.unresolvedCount, draftCount += child.draftCount, additions += child.additions, deletions += child.deletions;
    return {
      dirPath: target.dirPath,
      displayName: compacted.displayName,
      children,
      files,
      totalFileCount,
      reviewedFileCount,
      unresolvedCount,
      draftCount,
      additions,
      deletions
    };
  }
  function getBasename(filePath) {
    let slashIdx = filePath.lastIndexOf("/");
    return slashIdx >= 0 ? filePath.slice(slashIdx + 1) : filePath;
  }
  function buildCodeViewerDirectoryTree(files) {
    let root = createMutableDirNode("", "");
    for (let entry of files)
      !entry || !entry.file || entry.file === COMMIT_MSG_MAGIC_PATH || entry.file === MERGE_LIST_MAGIC_PATH || insertFileIntoMutableTree(root, entry);
    return finalizeDirNode(root, !0);
  }
  function CodeViewerToggleButton({
    clNumber,
    isActive = !1,
    fileCount = 0,
    compact = !1
  }) {
    if (!clNumber)
      return null;
    let countSuffix = fileCount > 0 && !compact ? ` (${fileCount})` : "", label = isActive ? "Exit Code Viewer" : compact ? "Code Viewer" : `Code Viewer${countSuffix}`;
    return /* @__PURE__ */ (0, import_jsx_runtime4.jsxs)(
      "button",
      {
        id: compact ? void 0 : `${BTN_CODE_VIEWER_PREFIX}${clNumber}`,
        type: "button",
        className: `btn pg-btn-header-action pg-btn-code-viewer ${isActive ? "is-active" : ""} ${compact ? "is-compact" : ""}`,
        htmlOnClick: `event.stopPropagation(); togglePgCodeViewerMode(${clNumber})`,
        title: "Toggle Commit Code Viewer directory tree sidebar (Shortcut: \\\\)",
        "aria-pressed": isActive ? "true" : "false",
        children: [
          /* @__PURE__ */ (0, import_jsx_runtime4.jsx)("span", { className: "pg-btn-icon", "aria-hidden": "true", children: isActive ? "\u2190" : "\u{1F4C2}" }),
          " ",
          /* @__PURE__ */ (0, import_jsx_runtime4.jsx)("span", { children: label })
        ]
      }
    );
  }
  function renderStatusBadge(status) {
    let norm = (status || "M").toUpperCase();
    return /* @__PURE__ */ (0, import_jsx_runtime4.jsx)(
      "span",
      {
        className: `pg-file-status-badge status-${norm} cv-status-badge`,
        title: `File status: ${norm}`,
        children: norm
      }
    );
  }
  function renderHighlightedBasename(name, filterQuery) {
    let trimmed = (filterQuery || "").trim();
    if (!trimmed)
      return name;
    let lowerName = name.toLowerCase(), lowerQuery = trimmed.toLowerCase(), idx = lowerName.indexOf(lowerQuery);
    if (idx < 0)
      return name;
    let before = name.slice(0, idx), match = name.slice(idx, idx + trimmed.length), after = name.slice(idx + trimmed.length);
    return /* @__PURE__ */ (0, import_jsx_runtime4.jsxs)(import_jsx_runtime4.Fragment, { children: [
      before,
      /* @__PURE__ */ (0, import_jsx_runtime4.jsx)("mark", { className: "cv-tree-match", children: match }),
      after
    ] });
  }
  function renderFileAnnotations(entry) {
    let unres = Number(entry.unresolvedCount || 0), drafts = Number(entry.draftCount || 0), totalComments = Number(entry.commentCount || 0);
    return /* @__PURE__ */ (0, import_jsx_runtime4.jsxs)(import_jsx_runtime4.Fragment, { children: [
      entry.isConflict ? /* @__PURE__ */ (0, import_jsx_runtime4.jsx)("span", { className: "cv-file-pill is-conflict", title: "Merge conflict", children: "!" }) : null,
      drafts > 0 ? /* @__PURE__ */ (0, import_jsx_runtime4.jsxs)(
        "span",
        {
          className: "cv-file-pill is-draft",
          title: `${drafts} draft comment(s)`,
          children: [
            "\u{1F4DD}",
            drafts
          ]
        }
      ) : null,
      unres > 0 ? /* @__PURE__ */ (0, import_jsx_runtime4.jsxs)(
        "span",
        {
          className: "cv-file-pill is-unresolved",
          title: `${unres} unresolved comment(s)`,
          children: [
            "\u26A0",
            unres
          ]
        }
      ) : totalComments > 0 ? /* @__PURE__ */ (0, import_jsx_runtime4.jsxs)(
        "span",
        {
          className: "cv-file-pill is-comment",
          title: `${totalComments} comment(s)`,
          children: [
            "\u{1F4AC}",
            totalComments
          ]
        }
      ) : null
    ] });
  }
  function renderDeltaSummary(additions, deletions) {
    let adds = Math.max(0, Number(additions || 0)), dels = Math.max(0, Number(deletions || 0));
    return adds === 0 && dels === 0 ? null : /* @__PURE__ */ (0, import_jsx_runtime4.jsxs)("span", { className: "pg-file-delta cv-file-delta", children: [
      adds > 0 ? /* @__PURE__ */ (0, import_jsx_runtime4.jsxs)("span", { className: "pg-delta-add", children: [
        "+",
        adds
      ] }) : null,
      dels > 0 ? /* @__PURE__ */ (0, import_jsx_runtime4.jsxs)("span", { className: "pg-delta-del", children: [
        "-",
        dels
      ] }) : null
    ] });
  }
  function FileLeafRow({
    clNumber,
    patchSet,
    entry,
    depth,
    selectedFile,
    filterQuery
  }) {
    let isSelected = entry.file === selectedFile, isReviewed = !!entry.isReviewed, indentPx = TREE_INDENT_BASE_PX + depth * TREE_INDENT_STEP_PX, jsFile = escapeJsQuote(entry.file), baseName = getBasename(entry.file);
    return /* @__PURE__ */ (0, import_jsx_runtime4.jsxs)(
      "div",
      {
        role: "treeitem",
        tabIndex: 0,
        "aria-selected": isSelected ? "true" : "false",
        className: `cv-tree-row cv-tree-file-row ${isSelected ? "is-selected" : ""} ${isReviewed ? "is-reviewed" : ""}`,
        "data-cv-cl": clNumber,
        "data-cv-file": entry.file,
        htmlStyle: `padding-left:${indentPx}px;`,
        htmlOnClick: `selectCodeViewerFile(${clNumber}, ${patchSet}, '${jsFile}', event)`,
        htmlOnKeyDown: `onCodeViewerFileKeyDown(event, ${clNumber}, ${patchSet}, '${jsFile}')`,
        title: entry.file,
        children: [
          /* @__PURE__ */ (0, import_jsx_runtime4.jsx)("span", { className: "pg-row-cursor cv-row-cursor", "aria-hidden": "true", children: "\u25B8" }),
          renderStatusBadge(entry.status),
          /* @__PURE__ */ (0, import_jsx_runtime4.jsx)("span", { className: "pg-tree-file-name cv-tree-file-name", children: renderHighlightedBasename(baseName, filterQuery) }),
          /* @__PURE__ */ (0, import_jsx_runtime4.jsxs)("span", { className: "cv-tree-file-meta", children: [
            renderFileAnnotations(entry),
            renderDeltaSummary(entry.additions, entry.deletions),
            /* @__PURE__ */ (0, import_jsx_runtime4.jsx)(
              "button",
              {
                type: "button",
                className: `cv-reviewed-toggle ${isReviewed ? "is-checked" : ""}`,
                htmlOnClick: `toggleCodeViewerFileReviewed(event, ${clNumber}, ${patchSet}, '${jsFile}')`,
                title: isReviewed ? `Reviewed (${entry.file}) \xB7 Click to mark unreviewed` : `Mark ${entry.file} as reviewed`,
                "aria-label": `Toggle reviewed for ${entry.file}`,
                children: "\u2713"
              }
            )
          ] })
        ]
      }
    );
  }
  function DirBranch({
    clNumber,
    patchSet,
    node,
    depth,
    selectedFile,
    collapsedDirs,
    filterQuery
  }) {
    let isCollapsed = !filterQuery && collapsedDirs.has(node.dirPath), indentPx = TREE_INDENT_BASE_PX + depth * TREE_INDENT_STEP_PX, jsDir = escapeJsQuote(node.dirPath), allReviewed = node.totalFileCount > 0 && node.reviewedFileCount === node.totalFileCount;
    return /* @__PURE__ */ (0, import_jsx_runtime4.jsxs)("div", { className: "cv-tree-group", role: "group", children: [
      /* @__PURE__ */ (0, import_jsx_runtime4.jsxs)(
        "div",
        {
          role: "treeitem",
          tabIndex: 0,
          "aria-expanded": isCollapsed ? "false" : "true",
          className: `cv-tree-row cv-tree-dir-row ${isCollapsed ? "is-collapsed" : ""} ${allReviewed ? "is-all-reviewed" : ""}`,
          "data-cv-dir": node.dirPath,
          htmlStyle: `padding-left:${indentPx}px;`,
          htmlOnClick: `toggleCodeViewerDir(${clNumber}, '${jsDir}')`,
          htmlOnKeyDown: `onCodeViewerDirKeyDown(event, ${clNumber}, '${jsDir}')`,
          title: `${node.dirPath} (${node.totalFileCount} files)`,
          children: [
            /* @__PURE__ */ (0, import_jsx_runtime4.jsx)("span", { className: "pg-dir-chevron cv-dir-chevron", "aria-hidden": "true", children: isCollapsed ? "\u25B8" : "\u25BE" }),
            /* @__PURE__ */ (0, import_jsx_runtime4.jsx)("span", { className: "pg-dir-icon cv-dir-icon", "aria-hidden": "true", children: "\u{1F4C1}" }),
            /* @__PURE__ */ (0, import_jsx_runtime4.jsxs)("span", { className: "pg-dir-name cv-dir-name", children: [
              node.displayName,
              "/"
            ] }),
            /* @__PURE__ */ (0, import_jsx_runtime4.jsxs)("span", { className: "cv-tree-file-meta", children: [
              node.unresolvedCount > 0 ? /* @__PURE__ */ (0, import_jsx_runtime4.jsxs)(
                "span",
                {
                  className: "cv-file-pill is-unresolved",
                  title: `${node.unresolvedCount} unresolved comment(s) in folder`,
                  children: [
                    "\u26A0",
                    node.unresolvedCount
                  ]
                }
              ) : null,
              /* @__PURE__ */ (0, import_jsx_runtime4.jsx)("span", { className: "pg-dir-count-pill cv-dir-count", children: node.totalFileCount })
            ] })
          ]
        }
      ),
      isCollapsed ? null : /* @__PURE__ */ (0, import_jsx_runtime4.jsxs)("div", { className: "cv-tree-children", children: [
        node.children.map((child) => /* @__PURE__ */ (0, import_jsx_runtime4.jsx)(
          DirBranch,
          {
            clNumber,
            patchSet,
            node: child,
            depth: depth + 1,
            selectedFile,
            collapsedDirs,
            filterQuery
          },
          child.dirPath
        )),
        node.files.map((fileEntry) => /* @__PURE__ */ (0, import_jsx_runtime4.jsx)(
          FileLeafRow,
          {
            clNumber,
            patchSet,
            entry: fileEntry,
            depth: depth + 1,
            selectedFile,
            filterQuery
          },
          fileEntry.file
        ))
      ] })
    ] });
  }
  function CodeViewerTopbar({
    displayMode
  }) {
    return /* @__PURE__ */ (0, import_jsx_runtime4.jsxs)("div", { className: "queue-viewer-toolbar pg-dashboard-bar cv-sidebar-topbar", children: [
      /* @__PURE__ */ (0, import_jsx_runtime4.jsxs)(
        "button",
        {
          id: "btnExitCodeViewerSidebar",
          type: "button",
          className: "pg-btn-subtle cv-back-dashboard-btn",
          htmlOnClick: "togglePgCodeViewerMode(null, false)",
          title: "Return to Dashboard commit queue sidebar (Shortcut: \\\\ or Esc)",
          children: [
            /* @__PURE__ */ (0, import_jsx_runtime4.jsx)("span", { "aria-hidden": "true", children: "\u2190" }),
            " Dashboard"
          ]
        }
      ),
      /* @__PURE__ */ (0, import_jsx_runtime4.jsxs)(
        "div",
        {
          className: "group-toggle cv-mode-toggle",
          role: "group",
          "aria-label": "Code viewer layout mode",
          title: "Choose whether clicking a file isolates its diff or scrolls within all files",
          children: [
            /* @__PURE__ */ (0, import_jsx_runtime4.jsx)(
              "button",
              {
                type: "button",
                className: displayMode === "single" ? "active" : "",
                htmlOnClick: "setCodeViewerDisplayMode('single')",
                title: "Show selected file diff cleanly in main viewer (preserves scroll per file)",
                children: "Focused"
              }
            ),
            /* @__PURE__ */ (0, import_jsx_runtime4.jsx)(
              "button",
              {
                type: "button",
                className: displayMode === "all" ? "active" : "",
                htmlOnClick: "setCodeViewerDisplayMode('all')",
                title: "Show all file diffs stacked and scroll to clicked file",
                children: "All Files"
              }
            )
          ]
        }
      ),
      /* @__PURE__ */ (0, import_jsx_runtime4.jsx)("div", { className: "cv-topbar-spacer" }),
      /* @__PURE__ */ (0, import_jsx_runtime4.jsx)(
        "button",
        {
          type: "button",
          className: "btn-sidebar-toggle",
          htmlOnClick: "toggleSidebarCollapsed(true)",
          title: "Collapse file tree panel ([)",
          "aria-label": "Collapse file tree panel",
          children: /* @__PURE__ */ (0, import_jsx_runtime4.jsxs)(
            "svg",
            {
              width: "14",
              height: "14",
              viewBox: "0 0 16 16",
              fill: "none",
              stroke: "currentColor",
              strokeWidth: "1.8",
              strokeLinecap: "round",
              strokeLinejoin: "round",
              "aria-hidden": "true",
              children: [
                /* @__PURE__ */ (0, import_jsx_runtime4.jsx)("rect", { x: "2", y: "2.5", width: "12", height: "11", rx: "2" }),
                /* @__PURE__ */ (0, import_jsx_runtime4.jsx)("line", { x1: "6.5", y1: "2.5", x2: "6.5", y2: "13.5" }),
                /* @__PURE__ */ (0, import_jsx_runtime4.jsx)("polyline", { points: "5 6.5 3.5 8 5 9.5" })
              ]
            }
          )
        }
      )
    ] });
  }
  function CodeViewerCommitHeader({
    clNumber,
    patchSet,
    subject,
    totalCount,
    canNavigateCommits
  }) {
    return /* @__PURE__ */ (0, import_jsx_runtime4.jsxs)(import_jsx_runtime4.Fragment, { children: [
      /* @__PURE__ */ (0, import_jsx_runtime4.jsxs)("div", { className: "pg-section-header cv-section-header", children: [
        /* @__PURE__ */ (0, import_jsx_runtime4.jsxs)("span", { className: "pg-section-title-wrap", children: [
          /* @__PURE__ */ (0, import_jsx_runtime4.jsx)("span", { className: "pg-section-name", children: "Changed Files" }),
          /* @__PURE__ */ (0, import_jsx_runtime4.jsxs)("span", { className: "pg-section-count", children: [
            "(",
            totalCount,
            ")"
          ] })
        ] }),
        /* @__PURE__ */ (0, import_jsx_runtime4.jsxs)("div", { className: "cv-cl-badge-group", title: subject, children: [
          canNavigateCommits ? /* @__PURE__ */ (0, import_jsx_runtime4.jsx)(
            "button",
            {
              type: "button",
              className: "cv-cl-step-btn",
              htmlOnClick: "navigateVisibleCl('prev')",
              title: "Previous commit in queue (k)",
              "aria-label": "Previous commit",
              children: "\u2039"
            }
          ) : null,
          /* @__PURE__ */ (0, import_jsx_runtime4.jsxs)("span", { className: "cv-cl-badge", children: [
            "ag/",
            clNumber,
            " ",
            /* @__PURE__ */ (0, import_jsx_runtime4.jsxs)("span", { className: "cv-cl-ps", children: [
              "\xB7 PS",
              patchSet
            ] })
          ] }),
          canNavigateCommits ? /* @__PURE__ */ (0, import_jsx_runtime4.jsx)(
            "button",
            {
              type: "button",
              className: "cv-cl-step-btn",
              htmlOnClick: "navigateVisibleCl('next')",
              title: "Next commit in queue (j)",
              "aria-label": "Next commit",
              children: "\u203A"
            }
          ) : null
        ] })
      ] }),
      /* @__PURE__ */ (0, import_jsx_runtime4.jsx)("div", { className: "cv-commit-meta-bar", title: subject, children: /* @__PURE__ */ (0, import_jsx_runtime4.jsx)("span", { className: "cv-commit-subj-text", children: subject || `Change ag/${clNumber}` }) })
    ] });
  }
  function CodeViewerControlsBar({
    clNumber,
    filterQuery,
    trimmedQuery,
    revCount,
    totalCount
  }) {
    let isAllRev = totalCount > 0 && revCount === totalCount;
    return /* @__PURE__ */ (0, import_jsx_runtime4.jsxs)("div", { className: "pg-file-controls-bar cv-controls-bar", children: [
      /* @__PURE__ */ (0, import_jsx_runtime4.jsxs)("div", { className: "cv-filter-wrap", children: [
        /* @__PURE__ */ (0, import_jsx_runtime4.jsx)("span", { className: "cv-filter-icon", "aria-hidden": "true", children: "\u{1F50D}" }),
        /* @__PURE__ */ (0, import_jsx_runtime4.jsx)(
          "input",
          {
            id: "cvTreeFilterInput",
            type: "text",
            className: "pg-file-filter-input cv-filter-input",
            placeholder: "Filter files\u2026",
            defaultValue: filterQuery,
            htmlOnInput: "onCodeViewerFilterInput(this.value)",
            htmlOnKeyDown: "onCodeViewerFilterKeyDown(event)",
            "aria-label": "Filter changed files in commit"
          }
        ),
        trimmedQuery ? /* @__PURE__ */ (0, import_jsx_runtime4.jsx)(
          "button",
          {
            type: "button",
            className: "cv-filter-clear-btn",
            htmlOnClick: "clearCodeViewerFilter()",
            title: "Clear file filter (Esc)",
            "aria-label": "Clear file filter",
            children: "\u2715"
          }
        ) : null
      ] }),
      /* @__PURE__ */ (0, import_jsx_runtime4.jsxs)("div", { className: "cv-tree-actions", children: [
        /* @__PURE__ */ (0, import_jsx_runtime4.jsxs)(
          "span",
          {
            id: `cvReviewedSummary-${clNumber}`,
            className: `pg-reviewed-summary-badge cv-reviewed-pill ${isAllRev ? "all-reviewed is-complete" : ""}`,
            title: `${revCount} of ${totalCount} changed files marked reviewed`,
            children: [
              revCount,
              "/",
              totalCount,
              " \u2713"
            ]
          }
        ),
        /* @__PURE__ */ (0, import_jsx_runtime4.jsx)(
          "button",
          {
            type: "button",
            className: "pg-btn-subtle cv-icon-btn",
            htmlOnClick: `expandAllCodeViewerDirs(${clNumber})`,
            title: "Expand all directories",
            "aria-label": "Expand all directories",
            children: "\u229E"
          }
        ),
        /* @__PURE__ */ (0, import_jsx_runtime4.jsx)(
          "button",
          {
            type: "button",
            className: "pg-btn-subtle cv-icon-btn",
            htmlOnClick: `collapseAllCodeViewerDirs(${clNumber})`,
            title: "Collapse all directories",
            "aria-label": "Collapse all directories",
            children: "\u229F"
          }
        )
      ] })
    ] });
  }
  function CodeViewerOverviewRow({
    clNumber,
    patchSet,
    isSelected,
    unresolvedCount
  }) {
    return /* @__PURE__ */ (0, import_jsx_runtime4.jsxs)(
      "div",
      {
        role: "treeitem",
        tabIndex: 0,
        "aria-selected": isSelected ? "true" : "false",
        className: `cv-tree-row cv-tree-overview-row ${isSelected ? "is-selected" : ""}`,
        "data-cv-cl": clNumber,
        "data-cv-file": COMMIT_MSG_MAGIC_PATH,
        htmlOnClick: `selectCodeViewerFile(${clNumber}, ${patchSet}, '${COMMIT_MSG_MAGIC_PATH}', event)`,
        htmlOnKeyDown: `onCodeViewerFileKeyDown(event, ${clNumber}, ${patchSet}, '${COMMIT_MSG_MAGIC_PATH}')`,
        title: "View Commit Message, Reviewers, Submit Requirements & Checks",
        children: [
          /* @__PURE__ */ (0, import_jsx_runtime4.jsx)("span", { className: "pg-row-cursor cv-row-cursor", "aria-hidden": "true", children: "\u25B8" }),
          /* @__PURE__ */ (0, import_jsx_runtime4.jsx)(
            "span",
            {
              className: "pg-file-status-badge status-M cv-overview-badge",
              "aria-hidden": "true",
              children: "\u{1F4C4}"
            }
          ),
          /* @__PURE__ */ (0, import_jsx_runtime4.jsx)("span", { className: "cv-overview-label", children: "Commit Overview & Message" }),
          /* @__PURE__ */ (0, import_jsx_runtime4.jsx)("span", { className: "cv-tree-file-meta", children: unresolvedCount > 0 ? /* @__PURE__ */ (0, import_jsx_runtime4.jsxs)(
            "span",
            {
              className: "cv-file-pill is-unresolved",
              title: `${unresolvedCount} unresolved comment(s) on commit message`,
              children: [
                "\u26A0",
                unresolvedCount
              ]
            }
          ) : null })
        ]
      }
    );
  }
  function renderTreeViewportBody(props) {
    if (props.isLoadingDiff)
      return /* @__PURE__ */ (0, import_jsx_runtime4.jsxs)("div", { className: "pg-empty-section-row cv-tree-empty", children: [
        "Loading changed files for ag/",
        props.clNumber,
        "\u2026"
      ] });
    if (props.filteredCount === 0) {
      let msg = props.trimmedQuery ? `No files matching "${props.trimmedQuery}"` : "No changed files in this patchset.";
      return /* @__PURE__ */ (0, import_jsx_runtime4.jsx)("div", { className: "pg-empty-section-row cv-tree-empty", children: msg });
    }
    return /* @__PURE__ */ (0, import_jsx_runtime4.jsxs)(import_jsx_runtime4.Fragment, { children: [
      props.rootTree.children.map((child) => /* @__PURE__ */ (0, import_jsx_runtime4.jsx)(
        DirBranch,
        {
          clNumber: props.clNumber,
          patchSet: props.patchSet,
          node: child,
          depth: 0,
          selectedFile: props.selectedFile,
          collapsedDirs: props.collapsedSet,
          filterQuery: props.trimmedQuery
        },
        child.dirPath
      )),
      props.rootTree.files.map((fileEntry) => /* @__PURE__ */ (0, import_jsx_runtime4.jsx)(
        FileLeafRow,
        {
          clNumber: props.clNumber,
          patchSet: props.patchSet,
          entry: fileEntry,
          depth: 0,
          selectedFile: props.selectedFile,
          filterQuery: props.filterQuery
        },
        fileEntry.file
      ))
    ] });
  }
  function CodeViewerTreeViewport(props) {
    return /* @__PURE__ */ (0, import_jsx_runtime4.jsxs)(
      "div",
      {
        id: "cvTreeScroll",
        className: "cv-tree-scroll",
        role: "tree",
        "aria-label": "Changed files directory tree",
        children: [
          /* @__PURE__ */ (0, import_jsx_runtime4.jsx)(
            CodeViewerOverviewRow,
            {
              clNumber: props.clNumber,
              patchSet: props.patchSet,
              isSelected: props.selectedFile === COMMIT_MSG_MAGIC_PATH,
              unresolvedCount: props.commitMsgUnresolvedCount
            }
          ),
          renderTreeViewportBody(props)
        ]
      }
    );
  }
  function CodeViewerSidebar({
    clNumber,
    patchSet,
    subject = "",
    files,
    selectedFile = "",
    collapsedDirs = [],
    filterQuery = "",
    displayMode = "single",
    totalFileCount,
    reviewedFileCount,
    commitMsgUnresolvedCount = 0,
    isLoadingDiff = !1,
    canNavigateCommits = !1
  }) {
    let collapsedSet = new Set(collapsedDirs), trimmedQuery = (filterQuery || "").trim(), lowerQuery = trimmedQuery.toLowerCase(), filteredFiles = lowerQuery ? files.filter((f) => String(f.file || "").toLowerCase().includes(lowerQuery)) : files, rootTree = buildCodeViewerDirectoryTree(filteredFiles), totalCount = totalFileCount !== void 0 ? totalFileCount : files.length, revCount = reviewedFileCount !== void 0 ? reviewedFileCount : files.filter((f) => !!f.isReviewed).length;
    return /* @__PURE__ */ (0, import_jsx_runtime4.jsxs)("div", { className: "cv-sidebar-inner", "data-cv-cl": clNumber, children: [
      /* @__PURE__ */ (0, import_jsx_runtime4.jsx)(CodeViewerTopbar, { displayMode }),
      /* @__PURE__ */ (0, import_jsx_runtime4.jsx)("div", { className: "cv-sidebar-canvas", children: /* @__PURE__ */ (0, import_jsx_runtime4.jsxs)("div", { className: "pg-section-box cv-section-box", children: [
        /* @__PURE__ */ (0, import_jsx_runtime4.jsx)(
          CodeViewerCommitHeader,
          {
            clNumber,
            patchSet,
            subject,
            totalCount,
            canNavigateCommits
          }
        ),
        /* @__PURE__ */ (0, import_jsx_runtime4.jsx)(
          CodeViewerControlsBar,
          {
            clNumber,
            filterQuery,
            trimmedQuery,
            revCount,
            totalCount
          }
        ),
        /* @__PURE__ */ (0, import_jsx_runtime4.jsx)(
          CodeViewerTreeViewport,
          {
            clNumber,
            patchSet,
            rootTree,
            filteredCount: filteredFiles.length,
            selectedFile,
            collapsedSet,
            filterQuery,
            trimmedQuery,
            commitMsgUnresolvedCount,
            isLoadingDiff
          }
        )
      ] }) })
    ] });
  }
  function renderPgCodeViewerToggleButtonHtml(props) {
    return renderReactToHtml(/* @__PURE__ */ (0, import_jsx_runtime4.jsx)(CodeViewerToggleButton, { ...props }));
  }
  function renderPgCodeViewerSidebarHtml(props) {
    return renderReactToHtml(/* @__PURE__ */ (0, import_jsx_runtime4.jsx)(CodeViewerSidebar, { ...props }));
  }

  // ../../../../../../../../execroot/_main/bazel-out/k8-fastbuild-ST-f481a530da89/bin/external/aemu+/tools/emu-dev-cli/src/ui/gerrit/components/ContinuousStackBanner.tsx
  var import_jsx_runtime5 = __toESM(require_jsx_runtime()), DEFAULT_STACK_TITLE = "Relation Stack", PERCENT_MULTIPLIER = 100;
  function computeStackDiffTotals(commits, bridge) {
    let totalAdd = 0, totalDel = 0, seenFiles = /* @__PURE__ */ new Set();
    for (let commit of commits) {
      let bundle = bridge.getDiffBundle(Number(commit.number)), files = Array.isArray(bundle?.files) ? bundle.files : [];
      for (let fileEntry of files)
        fileEntry.file && seenFiles.add(fileEntry.file), totalAdd += Number(fileEntry.additions || 0), totalDel += Number(fileEntry.deletions || 0);
    }
    return {
      totalAdd,
      totalDel,
      uniqueFilesCount: seenFiles.size
    };
  }
  function resolveStepperStatusIcon(commit, isSubmittable) {
    return commit.mergeable === !1 ? "\u25B2" : commit.work_in_progress ? "WIP" : isSubmittable ? "\u2713" : "";
  }
  function StackStepperRow({
    commits,
    currentNum,
    totalCommits,
    bridge
  }) {
    return /* @__PURE__ */ (0, import_jsx_runtime5.jsx)("div", { className: "pg-stack-stepper", children: commits.map((commit, idx) => {
      let num = Number(commit.number), isCurrent = num === currentNum, isReviewed = bridge.isStackCommitReviewed(num), isSubmittable = bridge.isClSubmittable(commit), statIcon = resolveStepperStatusIcon(commit, isSubmittable), pillCls = [
        "pg-stack-step-pill",
        isCurrent ? "active" : "",
        isReviewed ? "is-reviewed" : "",
        isSubmittable ? "ready" : ""
      ].filter(Boolean).join(" "), revSuffix = isReviewed ? " (Reviewed)" : "";
      return /* @__PURE__ */ (0, import_jsx_runtime5.jsxs)(
        "button",
        {
          type: "button",
          className: pillCls,
          "data-cl": num,
          htmlOnClick: `scrollToStackCommit(${num})`,
          title: `Commit ${idx + 1}/${totalCommits}: ag/${num} - ${commit.subject || ""}${revSuffix}`,
          children: [
            /* @__PURE__ */ (0, import_jsx_runtime5.jsx)("span", { className: "pg-step-idx", children: idx + 1 }),
            /* @__PURE__ */ (0, import_jsx_runtime5.jsx)("span", { className: "pg-step-cl", children: `ag/${num}` }),
            isReviewed ? /* @__PURE__ */ (0, import_jsx_runtime5.jsx)("span", { className: "pg-step-rev-icon", title: "Commit reviewed", children: "\u2713" }) : null,
            statIcon ? /* @__PURE__ */ (0, import_jsx_runtime5.jsx)("span", { className: "pg-step-status", children: statIcon }) : null
          ]
        },
        num
      );
    }) });
  }
  function ContinuousStackBanner({
    activeStack,
    commits,
    bridge
  }) {
    if (!activeStack || !Array.isArray(commits) || commits.length <= 1)
      return null;
    let activeBridge = bridge ?? createHostBridge(), totalCommits = commits.length, { totalAdd, totalDel, uniqueFilesCount } = computeStackDiffTotals(
      commits,
      activeBridge
    ), selectedCl = activeBridge.getSelectedClNumber(), currentNum = Number(selectedCl || commits[0]?.number || 0), currentIdx = commits.findIndex(
      (commit) => Number(commit.number) === currentNum
    ), reviewedCount = commits.filter(
      (commit) => activeBridge.isStackCommitReviewed(Number(commit.number))
    ).length, pctReviewed = Math.round(
      reviewedCount / totalCommits * PERCENT_MULTIPLIER
    ), allReviewed = reviewedCount === totalCommits, anyExpanded = commits.some(
      (commit) => !activeBridge.isStackCommitCollapsed(Number(commit.number))
    ), seq = activeBridge.getStackSubmitSequence(activeStack, commits), stackId = activeStack.stack_id || "", escapedStackId = escapeHtml(stackId), stackTitle = activeStack.title || activeStack.stack_id || DEFAULT_STACK_TITLE, canInterdiff = activeBridge.canStackInterdiff(commits), isStackInterdiff = activeBridge.isStackInterdiffActive(commits, stackId);
    canInterdiff && !isStackInterdiff && activeBridge.getPersistedStackInterdiff(stackId) && (activeBridge.ensureStackInterdiffRestored(activeStack), isStackInterdiff = !0);
    let bottleneckNum = seq.bottleneck_commit?.number ?? "", seqTitle = seq.all_ready ? "Entire stack is submittable in sequence! (Alt+T)" : `Bottleneck at Step ${seq.bottleneck_step}: ag/${bottleneckNum} (${seq.bottleneck_reason || ""}) (Alt+T)`;
    return /* @__PURE__ */ (0, import_jsx_runtime5.jsxs)(
      "div",
      {
        id: "pgStackReviewBanner",
        className: "pg-stack-review-banner",
        "data-stack-id": stackId,
        children: [
          /* @__PURE__ */ (0, import_jsx_runtime5.jsxs)("div", { className: "pg-stack-review-banner-top", children: [
            /* @__PURE__ */ (0, import_jsx_runtime5.jsxs)("div", { className: "pg-stack-review-banner-left", children: [
              /* @__PURE__ */ (0, import_jsx_runtime5.jsx)("span", { className: "pg-stack-review-badge", children: "\u{1F4DA} Continuous Stack Review" }),
              isStackInterdiff ? /* @__PURE__ */ (0, import_jsx_runtime5.jsx)(
                "span",
                {
                  id: "pgStackInterdiffBadge",
                  className: "pg-stack-interdiff-badge",
                  title: "Comparing patchsets PS n-1 \u2192 PS n across stack commits",
                  children: "\u26A1 Stack Interdiff (PS n-1)"
                }
              ) : null,
              /* @__PURE__ */ (0, import_jsx_runtime5.jsx)("span", { className: "pg-stack-review-title", title: stackTitle, children: `${stackTitle} (${totalCommits} commits)` }),
              /* @__PURE__ */ (0, import_jsx_runtime5.jsxs)(
                "span",
                {
                  className: "pg-stack-review-stats is-clickable",
                  htmlOnClick: `openPgStackFilesDrawer('${escapedStackId}')`,
                  title: "Inspect cumulative stack files (Alt+F)",
                  children: [
                    /* @__PURE__ */ (0, import_jsx_runtime5.jsx)("span", { className: "pg-files-add", children: `+${totalAdd}` }),
                    " ",
                    /* @__PURE__ */ (0, import_jsx_runtime5.jsx)("span", { className: "pg-files-del", children: `-${totalDel}` }),
                    ` across ${uniqueFilesCount} files`
                  ]
                }
              )
            ] }),
            /* @__PURE__ */ (0, import_jsx_runtime5.jsxs)("div", { className: "pg-stack-progress-block", children: [
              /* @__PURE__ */ (0, import_jsx_runtime5.jsxs)("div", { className: "pg-stack-progress-labels", children: [
                /* @__PURE__ */ (0, import_jsx_runtime5.jsx)("span", { id: "pgStackProgressText", className: "pg-stack-progress-text", children: `${reviewedCount} of ${totalCommits} commits reviewed (${pctReviewed}%)` }),
                /* @__PURE__ */ (0, import_jsx_runtime5.jsx)(
                  "span",
                  {
                    id: "pgStackCompleteBadge",
                    className: "pg-stack-complete-badge",
                    htmlStyle: allReviewed ? "display:inline-flex;" : "display:none;",
                    children: "\u{1F389} Whole Stack Reviewed!"
                  }
                ),
                /* @__PURE__ */ (0, import_jsx_runtime5.jsx)(
                  "span",
                  {
                    id: "pgStackSequenceStatusBadge",
                    className: `pg-stack-seq-status-badge ${seq.all_ready ? "ready" : "pending"}`,
                    htmlOnClick: `openPgStackSequenceDialog('${escapedStackId}')`,
                    title: seqTitle,
                    children: seq.all_ready ? "\u26A1 Stack Ready" : `\u23F3 Step ${seq.bottleneck_step}/${totalCommits} Next`
                  }
                )
              ] }),
              /* @__PURE__ */ (0, import_jsx_runtime5.jsx)("div", { className: "pg-stack-progress-track", children: /* @__PURE__ */ (0, import_jsx_runtime5.jsx)(
                "div",
                {
                  id: "pgStackProgressFill",
                  className: "pg-stack-progress-fill",
                  htmlStyle: `width: ${pctReviewed}%;`
                }
              ) })
            ] }),
            /* @__PURE__ */ (0, import_jsx_runtime5.jsxs)("div", { className: "pg-stack-review-actions", children: [
              /* @__PURE__ */ (0, import_jsx_runtime5.jsxs)(
                "button",
                {
                  id: "btnStackBatchApproveAll",
                  type: "button",
                  className: "btn pg-btn-stack-action pg-btn-stack-approve",
                  htmlOnClick: `approveStackForBatchEdit('${escapedStackId}')`,
                  title: `Approve all ${totalCommits} commits with Code-Review +2`,
                  children: [
                    /* @__PURE__ */ (0, import_jsx_runtime5.jsx)("span", { className: "pg-btn-icon", children: "\u26A1" }),
                    " +2 All Commits"
                  ]
                }
              ),
              /* @__PURE__ */ (0, import_jsx_runtime5.jsxs)(
                "button",
                {
                  id: "btnStackBatchSelectAll",
                  type: "button",
                  className: "btn pg-btn-stack-action",
                  htmlOnClick: `selectStackForBatchEdit('${escapedStackId}')`,
                  title: `Select all ${totalCommits} commits in this stack for Batch Edit`,
                  children: [
                    /* @__PURE__ */ (0, import_jsx_runtime5.jsx)("span", { className: "pg-btn-icon", children: "\u2713" }),
                    " Batch Select All"
                  ]
                }
              ),
              /* @__PURE__ */ (0, import_jsx_runtime5.jsxs)(
                "button",
                {
                  id: "btnToggleStackInterdiff",
                  type: "button",
                  className: `btn pg-btn-stack-action pg-btn-stack-interdiff${isStackInterdiff ? " is-active" : ""}`,
                  htmlOnClick: `togglePgStackInterdiff('${escapedStackId}')`,
                  title: isStackInterdiff ? "Reset comparison across all commits in stack back to Base (Alt+I or b)" : "Compare all multi-patchset commits in stack against their previous patchsets (Alt+I or b)",
                  disabled: !canInterdiff,
                  children: [
                    /* @__PURE__ */ (0, import_jsx_runtime5.jsx)("span", { className: "pg-btn-icon", children: isStackInterdiff ? "\u2715" : "\u26A1" }),
                    " ",
                    isStackInterdiff ? "Reset Stack vs Base" : "Stack Interdiff (PS n-1)"
                  ]
                }
              ),
              /* @__PURE__ */ (0, import_jsx_runtime5.jsx)(
                "button",
                {
                  id: "btnStackToggleCollapseAll",
                  type: "button",
                  className: "btn pg-btn-stack-action",
                  htmlOnClick: "toggleAllStackCommitsCollapse()",
                  title: "Toggle collapse/expand all commit bodies in this stack",
                  children: anyExpanded ? "\u25B2 Collapse All" : "\u25BC Expand All"
                }
              ),
              /* @__PURE__ */ (0, import_jsx_runtime5.jsx)(
                "button",
                {
                  id: "btnExitContinuousStack",
                  type: "button",
                  className: "btn pg-btn-stack-action pg-btn-stack-exit",
                  htmlOnClick: `toggleContinuousStackDiff(event, '${escapedStackId}')`,
                  title: "Exit continuous stack review and focus single CL",
                  children: "\u{1F4C4} Single CL View"
                }
              )
            ] })
          ] }),
          /* @__PURE__ */ (0, import_jsx_runtime5.jsxs)("div", { className: "pg-stack-review-banner-bottom", children: [
            /* @__PURE__ */ (0, import_jsx_runtime5.jsxs)("div", { className: "pg-stack-stepper-wrap", children: [
              /* @__PURE__ */ (0, import_jsx_runtime5.jsx)(
                "button",
                {
                  id: "btnStackPrevCommit",
                  type: "button",
                  className: "btn pg-btn-stepper-nav",
                  htmlOnClick: "goToPrevStackCommit()",
                  disabled: currentIdx <= 0,
                  title: "Jump to previous commit in stack (Shift+[)",
                  children: "\u25C0 Prev"
                }
              ),
              /* @__PURE__ */ (0, import_jsx_runtime5.jsx)(
                StackStepperRow,
                {
                  commits,
                  currentNum,
                  totalCommits,
                  bridge: activeBridge
                }
              ),
              /* @__PURE__ */ (0, import_jsx_runtime5.jsx)(
                "button",
                {
                  id: "btnStackNextCommit",
                  type: "button",
                  className: "btn pg-btn-stepper-nav",
                  htmlOnClick: "goToNextStackCommit()",
                  disabled: currentIdx >= totalCommits - 1,
                  title: "Jump to next commit in stack (Shift+])",
                  children: "Next \u25B6"
                }
              )
            ] }),
            /* @__PURE__ */ (0, import_jsx_runtime5.jsxs)("div", { className: "pg-stack-checklist-actions", children: [
              /* @__PURE__ */ (0, import_jsx_runtime5.jsxs)(
                "button",
                {
                  id: "btnStackFilesDrawer",
                  type: "button",
                  className: "btn pg-btn-stack-action pg-btn-stack-files",
                  htmlOnClick: `openPgStackFilesDrawer('${escapedStackId}')`,
                  title: "Overview and jump to any file touched across the whole stack (Alt+F)",
                  children: [
                    /* @__PURE__ */ (0, import_jsx_runtime5.jsx)("span", { className: "pg-btn-icon", children: "\u{1F4C1}" }),
                    " ",
                    `Stack Files (${uniqueFilesCount})`
                  ]
                }
              ),
              /* @__PURE__ */ (0, import_jsx_runtime5.jsxs)(
                "button",
                {
                  id: "btnStackSubmitSeq",
                  type: "button",
                  className: `btn pg-btn-stack-action pg-btn-stack-seq${seq.all_ready ? " is-ready" : ""}`,
                  htmlOnClick: `openPgStackSequenceDialog('${escapedStackId}')`,
                  title: "Inspect stack submit sequence & checkpoints (Alt+T)",
                  children: [
                    /* @__PURE__ */ (0, import_jsx_runtime5.jsx)("span", { className: "pg-btn-icon", children: seq.all_ready ? "\u2713" : "\u{1F4CB}" }),
                    " ",
                    `Submit Sequence (${seq.seq_ready_count}/${totalCommits} Ready)`
                  ]
                }
              ),
              /* @__PURE__ */ (0, import_jsx_runtime5.jsx)(
                "button",
                {
                  id: "btnStackMarkAllReviewed",
                  type: "button",
                  className: "pg-btn-text-action",
                  htmlOnClick: allReviewed ? `resetStackCommitsReviewed('${escapedStackId}')` : `markAllStackCommitsReviewed('${escapedStackId}')`,
                  children: allReviewed ? "Reset Reviews" : "Mark All Reviewed \u2713"
                }
              )
            ] })
          ] })
        ]
      }
    );
  }
  function SequenceStepStatusBadge({
    itemSeq,
    idx,
    totalCount
  }) {
    if (!itemSeq)
      return null;
    let stepNum = idx + 1;
    if (itemSeq.seq_status === "ready")
      return /* @__PURE__ */ (0, import_jsx_runtime5.jsx)(
        "span",
        {
          className: "pg-stack-seq-step-badge is-ready",
          title: `Step ${stepNum} of ${totalCount}: Ready to submit in topological sequence`,
          children: `\u2713 Step ${stepNum} Ready`
        }
      );
    if (itemSeq.seq_status === "blocked_current") {
      let reason = itemSeq.blocker_reason || "";
      return /* @__PURE__ */ (0, import_jsx_runtime5.jsx)(
        "span",
        {
          className: "pg-stack-seq-step-badge is-blocked",
          title: `Step ${stepNum} of ${totalCount} is blocking stack progress: ${reason}`,
          children: `\u2715 Step ${stepNum} Blocked: ${reason}`
        }
      );
    }
    if (itemSeq.seq_status === "waiting_ancestor") {
      let ancNum = itemSeq.blocking_ancestor?.number ?? "";
      return /* @__PURE__ */ (0, import_jsx_runtime5.jsx)(
        "span",
        {
          className: "pg-stack-seq-step-badge is-waiting",
          title: `Step ${stepNum} of ${totalCount}: Waiting on parent ag/${ancNum} (Step ${itemSeq.blocking_step})`,
          children: `\u23F3 Step ${stepNum} Waiting (on ag/${ancNum})`
        }
      );
    }
    return itemSeq.seq_status === "merged" ? /* @__PURE__ */ (0, import_jsx_runtime5.jsx)(
      "span",
      {
        className: "pg-stack-seq-step-badge is-merged",
        title: `Step ${stepNum} of ${totalCount}: Already merged`,
        children: "Merged"
      }
    ) : null;
  }
  function StackCommitSequenceBar({
    change,
    idx,
    totalCount,
    activeStack,
    bridge
  }) {
    if (!change)
      return null;
    let activeBridge = bridge ?? createHostBridge(), num = Number(change.number), commits = Array.isArray(activeStack?.commits) ? activeStack.commits : [], isSubmittable = activeBridge.isClSubmittable(change), isReviewed = activeBridge.isStackCommitReviewed(num), isCollapsed = activeBridge.isStackCommitCollapsed(num), bundle = activeBridge.getDiffBundle(num), bundleFiles = Array.isArray(bundle?.files) ? bundle.files : null, fileCount = bundleFiles ? bundleFiles.length : Array.isArray(change.files) ? change.files.length : 0, adds = bundleFiles ? bundleFiles.reduce((acc, f) => acc + Number(f.additions || 0), 0) : 0, dels = bundleFiles ? bundleFiles.reduce((acc, f) => acc + Number(f.deletions || 0), 0) : 0, summaryText = `Collapsed \xB7 ${fileCount} file${fileCount === 1 ? "" : "s"} (+${adds}/-${dels}) \xB7 Click to expand`, seq = activeBridge.getStackSubmitSequence(activeStack, commits), itemSeq = Array.isArray(seq?.items) ? seq.items.find((it) => it.number === num) : null, prevCommit = idx > 0 ? commits[idx - 1] : null, nextCommit = idx < totalCount - 1 ? commits[idx + 1] : null;
    return /* @__PURE__ */ (0, import_jsx_runtime5.jsxs)(
      "div",
      {
        className: `pg-stack-commit-seq-bar${isCollapsed ? " is-collapsed" : ""}`,
        htmlOnClick: `togglePgStackCommitCollapse(${num})`,
        children: [
          /* @__PURE__ */ (0, import_jsx_runtime5.jsxs)("div", { className: "pg-stack-commit-seq-left", children: [
            /* @__PURE__ */ (0, import_jsx_runtime5.jsx)(
              "button",
              {
                id: `btnToggleStackCommitCollapse-${num}`,
                type: "button",
                className: "pg-btn-stack-commit-toggle",
                htmlOnClick: `event.stopPropagation(); togglePgStackCommitCollapse(${num})`,
                title: isCollapsed ? "Expand commit diff" : "Collapse commit diff",
                children: /* @__PURE__ */ (0, import_jsx_runtime5.jsx)("span", { className: "pg-stack-collapse-icon", children: isCollapsed ? "\u25B6" : "\u25BC" })
              }
            ),
            /* @__PURE__ */ (0, import_jsx_runtime5.jsxs)("div", { className: "pg-stack-commit-seq-pill", children: [
              /* @__PURE__ */ (0, import_jsx_runtime5.jsx)("span", { children: `Commit ${idx + 1} of ${totalCount}` }),
              /* @__PURE__ */ (0, import_jsx_runtime5.jsx)("span", { className: "pg-seq-sep", children: "\xB7" }),
              /* @__PURE__ */ (0, import_jsx_runtime5.jsx)(
                "a",
                {
                  href: `#pgStackCommit-${num}`,
                  className: "pg-seq-cl-link",
                  htmlOnClick: `event.preventDefault(); event.stopPropagation(); scrollToStackCommit(${num})`,
                  children: `ag/${num}`
                }
              ),
              isSubmittable ? /* @__PURE__ */ (0, import_jsx_runtime5.jsx)("span", { className: "pg-status-badge status-submittable", children: "\u2713 Ready" }) : /* @__PURE__ */ (0, import_jsx_runtime5.jsx)("span", { className: "pg-status-badge status-review", children: "In Review" }),
              /* @__PURE__ */ (0, import_jsx_runtime5.jsx)(
                SequenceStepStatusBadge,
                {
                  itemSeq,
                  idx,
                  totalCount
                }
              )
            ] }),
            /* @__PURE__ */ (0, import_jsx_runtime5.jsxs)(
              "button",
              {
                id: `btnToggleStackCommitReviewed-${num}`,
                type: "button",
                className: `pg-btn-stack-reviewed${isReviewed ? " is-reviewed" : ""}`,
                htmlOnClick: `event.stopPropagation(); togglePgStackCommitReviewed(${num})`,
                title: isReviewed ? `ag/${num}: Reviewed (click to mark unreviewed)` : `Mark commit ag/${num} as reviewed (Shift+Enter)`,
                children: [
                  /* @__PURE__ */ (0, import_jsx_runtime5.jsx)("span", { className: "pg-btn-icon", "aria-hidden": "true", children: isReviewed ? "\u2713" : "\u25CB" }),
                  /* @__PURE__ */ (0, import_jsx_runtime5.jsx)("span", { className: "pg-btn-label", children: isReviewed ? "Commit Reviewed" : "Mark Reviewed" })
                ]
              }
            ),
            /* @__PURE__ */ (0, import_jsx_runtime5.jsx)(
              "span",
              {
                id: `pgStackCommitSummary-${num}`,
                className: "pg-stack-collapsed-summary",
                htmlStyle: isCollapsed ? "" : "display:none;",
                children: summaryText
              }
            )
          ] }),
          /* @__PURE__ */ (0, import_jsx_runtime5.jsxs)("div", { className: "pg-stack-commit-seq-right", children: [
            /* @__PURE__ */ (0, import_jsx_runtime5.jsxs)("div", { className: "pg-stack-seq-nav", children: [
              prevCommit ? /* @__PURE__ */ (0, import_jsx_runtime5.jsx)(
                "button",
                {
                  type: "button",
                  className: "pg-btn-stack-nav",
                  htmlOnClick: `event.stopPropagation(); scrollToStackCommit(${prevCommit.number})`,
                  title: `Jump to previous commit: ag/${prevCommit.number}`,
                  children: `\u25C0 Prev (ag/${prevCommit.number})`
                }
              ) : null,
              nextCommit ? /* @__PURE__ */ (0, import_jsx_runtime5.jsx)(
                "button",
                {
                  type: "button",
                  className: "pg-btn-stack-nav",
                  htmlOnClick: `event.stopPropagation(); scrollToStackCommit(${nextCommit.number})`,
                  title: `Jump to next commit: ag/${nextCommit.number}`,
                  children: `Next (ag/${nextCommit.number}) \u25B6`
                }
              ) : null
            ] }),
            /* @__PURE__ */ (0, import_jsx_runtime5.jsx)("span", { className: "pg-seq-subject", title: change.subject || "", children: change.subject || "" }),
            /* @__PURE__ */ (0, import_jsx_runtime5.jsx)(
              "a",
              {
                href: "javascript:void(0)",
                className: "pg-stack-commit-jump-top",
                htmlOnClick: "event.stopPropagation(); scrollDiffToTop()",
                title: "Jump to top of stack",
                children: "\u2191 Top of Stack"
              }
            )
          ] })
        ]
      }
    );
  }
  function renderPgContinuousStackBannerHtml(activeStack, commits, bridge) {
    return renderReactToHtml(
      /* @__PURE__ */ (0, import_jsx_runtime5.jsx)(
        ContinuousStackBanner,
        {
          activeStack,
          commits,
          bridge
        }
      )
    );
  }
  function renderPgStackCommitSequenceBarHtml(change, idx = 0, totalCount = 0, activeStack, bridge) {
    return renderReactToHtml(
      /* @__PURE__ */ (0, import_jsx_runtime5.jsx)(
        StackCommitSequenceBar,
        {
          change,
          idx,
          totalCount,
          activeStack,
          bridge
        }
      )
    );
  }

  // ../../../../../../../../execroot/_main/bazel-out/k8-fastbuild-ST-f481a530da89/bin/external/aemu+/tools/emu-dev-cli/src/ui/gerrit/components/DashboardQueueTable.tsx
  var import_jsx_runtime6 = __toESM(require_jsx_runtime());
  function CommitSubmitButton({
    target,
    isBusy = !1
  }) {
    let isDisabled = !target.canSubmit || isBusy;
    return /* @__PURE__ */ (0, import_jsx_runtime6.jsx)(
      "button",
      {
        id: "btnSubmitCl",
        type: "button",
        className: "pg-btn-primary btn-submit-cl",
        htmlStyle: target.canSubmit ? void 0 : "display:none;",
        disabled: isDisabled,
        "aria-disabled": isDisabled ? "true" : "false",
        htmlOnClick: "event.stopPropagation(); submitActiveClOrTopic(this)",
        title: target.title,
        children: target.label
      }
    );
  }
  function BatchSubmitButton({
    target,
    isBusy = !1
  }) {
    let isDisabled = !target.canSubmit || isBusy;
    return /* @__PURE__ */ (0, import_jsx_runtime6.jsx)(
      "button",
      {
        id: "btnBatchSubmitCls",
        type: "button",
        className: "pg-btn-primary btn btn-submit-cl pg-batch-btn-submit",
        disabled: isDisabled,
        "aria-disabled": isDisabled ? "true" : "false",
        htmlOnClick: "event.stopPropagation(); submitBatchCheckedCls(this)",
        title: target.title,
        children: target.label
      }
    );
  }
  function formatCr2WhoList(rawHumanCr2) {
    return rawHumanCr2.map((item) => {
      if (typeof item == "string")
        return item;
      if (item && typeof item == "object") {
        let v = item;
        return v.short || v.name || v.email || "";
      }
      return "";
    }).filter(Boolean).join(", ");
  }
  function CrVoteCell({
    change,
    reviewers = []
  }) {
    let humanRevs = reviewers.filter((p) => !p.is_bot), minVote = 0;
    for (let rev of humanRevs) {
      let v = Number(rev.cr_vote || 0);
      v < minVote && (minVote = v);
    }
    if (minVote <= -2)
      return /* @__PURE__ */ (0, import_jsx_runtime6.jsx)(
        "span",
        {
          className: "pg-vote-chip v-rej",
          title: "Code-Review -2 (Do not submit)",
          children: "-2"
        }
      );
    if (minVote === -1)
      return /* @__PURE__ */ (0, import_jsx_runtime6.jsx)("span", { className: "pg-vote-chip v-disliked", title: "Code-Review -1", children: "-1" });
    let reqCount = Number(
      change.required_cr2_count || (change.is_agent_cl ? 2 : 1)
    ), humanCr2 = Array.isArray(change.human_cr2) ? change.human_cr2 : [], doneCount = humanCr2.length || Number(change.human_cr2_count || 0);
    if (doneCount >= reqCount && reqCount > 0) {
      let who = formatCr2WhoList(humanCr2), tip = who ? `Code-Review +2 satisfied (${who})` : `Code-Review +2 satisfied (${doneCount}/${reqCount})`;
      return /* @__PURE__ */ (0, import_jsx_runtime6.jsx)("span", { className: "pg-vote-chip v-approved", title: tip, children: "\u2713+2" });
    }
    if (doneCount > 0)
      return /* @__PURE__ */ (0, import_jsx_runtime6.jsx)(
        "span",
        {
          className: "pg-vote-chip v-rec",
          title: `Code-Review: ${doneCount}/${reqCount} human +2 approvals (needs 1 more +2)`,
          children: "+2\xB7\xBD"
        }
      );
    if (Array.isArray(change.human_cr1) && change.human_cr1.length > 0 || humanRevs.some((p) => Number(p.cr_vote || 0) === 1))
      return /* @__PURE__ */ (0, import_jsx_runtime6.jsx)(
        "span",
        {
          className: "pg-vote-chip v-rec",
          title: `Code-Review +1 (needs +${2})`,
          children: "+1"
        }
      );
    let needTip = reqCount >= 2 ? "Code-Review: Needs 2 human +2 approvals (agent CL)" : "Code-Review: Needs +2 approval";
    return /* @__PURE__ */ (0, import_jsx_runtime6.jsx)("span", { className: "pg-vote-empty", title: needTip, children: "\u2014" });
  }
  function VVoteBody({
    display
  }) {
    return /* @__PURE__ */ (0, import_jsx_runtime6.jsx)("span", { className: display.cls, title: display.tooltip, children: display.text });
  }
  function VVoteCell({
    display
  }) {
    return /* @__PURE__ */ (0, import_jsx_runtime6.jsx)("span", { className: "pg-v-chip-wrap", title: display.tooltip, children: /* @__PURE__ */ (0, import_jsx_runtime6.jsx)(VVoteBody, { display }) });
  }
  function PvVoteBody({
    display
  }) {
    return display.status === "passed" ? /* @__PURE__ */ (0, import_jsx_runtime6.jsx)("span", { className: "pg-vote-chip v-approved", title: display.tooltip, children: "\u2713" }) : display.status === "warning" ? /* @__PURE__ */ (0, import_jsx_runtime6.jsx)("span", { className: "pg-vote-chip v-rec", title: display.tooltip, children: "\u26A0+1" }) : display.status === "failed" ? /* @__PURE__ */ (0, import_jsx_runtime6.jsx)("span", { className: "pg-vote-chip v-rej", title: display.tooltip, children: "\u2717" }) : display.status === "running" || display.status === "queued" ? /* @__PURE__ */ (0, import_jsx_runtime6.jsx)(
      "span",
      {
        className: "pg-vote-chip",
        htmlStyle: "color:var(--g-accent);",
        title: display.tooltip,
        children: /* @__PURE__ */ (0, import_jsx_runtime6.jsx)("span", { className: "pv-mini-spinner" })
      }
    ) : /* @__PURE__ */ (0, import_jsx_runtime6.jsx)("span", { className: "pg-vote-empty", title: display.tooltip, children: "\u2014" });
  }
  function PvVoteCell({
    clNumber,
    stackId = "",
    display
  }) {
    let escapedStackId = escapeHtml(stackId || "");
    return /* @__PURE__ */ (0, import_jsx_runtime6.jsx)(
      "button",
      {
        type: "button",
        className: "pg-pv-cell-btn",
        "data-pv-cl": clNumber,
        htmlOnClick: `event.stopPropagation(); openPresubmitTargetsFromCard('${escapedStackId}', ${clNumber})`,
        title: display.tooltip,
        children: /* @__PURE__ */ (0, import_jsx_runtime6.jsx)(PvVoteBody, { display })
      }
    );
  }
  function DashboardStatusTag({
    flags
  }) {
    return flags.isMerged ? /* @__PURE__ */ (0, import_jsx_runtime6.jsxs)("span", { className: "pg-mini-status st-merged", title: "Merged", children: [
      /* @__PURE__ */ (0, import_jsx_runtime6.jsx)("span", { className: "pg-status-full", children: "Merged" }),
      /* @__PURE__ */ (0, import_jsx_runtime6.jsx)("span", { className: "pg-status-mini", children: "Merged" })
    ] }) : flags.isAbandoned ? /* @__PURE__ */ (0, import_jsx_runtime6.jsxs)("span", { className: "pg-mini-status st-abandoned", title: "Abandoned", children: [
      /* @__PURE__ */ (0, import_jsx_runtime6.jsx)("span", { className: "pg-status-full", children: "Abandoned" }),
      /* @__PURE__ */ (0, import_jsx_runtime6.jsx)("span", { className: "pg-status-mini", children: "Aband." })
    ] }) : flags.hasConflict ? /* @__PURE__ */ (0, import_jsx_runtime6.jsxs)(import_jsx_runtime6.Fragment, { children: [
      /* @__PURE__ */ (0, import_jsx_runtime6.jsxs)(
        "span",
        {
          className: "pg-mini-status st-conflict",
          title: "Merge Conflict \u2014 rebase required",
          children: [
            /* @__PURE__ */ (0, import_jsx_runtime6.jsx)("span", { className: "pg-status-full", children: "Merge Conflict" }),
            /* @__PURE__ */ (0, import_jsx_runtime6.jsx)("span", { className: "pg-status-mini", children: "Conflict" })
          ]
        }
      ),
      flags.isWip ? /* @__PURE__ */ (0, import_jsx_runtime6.jsxs)(import_jsx_runtime6.Fragment, { children: [
        " ",
        /* @__PURE__ */ (0, import_jsx_runtime6.jsxs)("span", { className: "pg-mini-status st-wip", title: "Work in Progress", children: [
          /* @__PURE__ */ (0, import_jsx_runtime6.jsx)("span", { className: "pg-status-full", children: "WIP" }),
          /* @__PURE__ */ (0, import_jsx_runtime6.jsx)("span", { className: "pg-status-mini", children: "WIP" })
        ] })
      ] }) : null
    ] }) : flags.isWip ? /* @__PURE__ */ (0, import_jsx_runtime6.jsxs)("span", { className: "pg-mini-status st-wip", title: "Work in Progress", children: [
      /* @__PURE__ */ (0, import_jsx_runtime6.jsx)("span", { className: "pg-status-full", children: "WIP" }),
      /* @__PURE__ */ (0, import_jsx_runtime6.jsx)("span", { className: "pg-status-mini", children: "WIP" })
    ] }) : flags.isCcOnly ? /* @__PURE__ */ (0, import_jsx_runtime6.jsxs)(
      "span",
      {
        className: "pg-mini-status st-cc",
        title: "You are CCed on this change",
        children: [
          /* @__PURE__ */ (0, import_jsx_runtime6.jsx)("span", { className: "pg-status-full", children: "CC" }),
          /* @__PURE__ */ (0, import_jsx_runtime6.jsx)("span", { className: "pg-status-mini", children: "CC" })
        ]
      }
    ) : flags.isSubmittable ? /* @__PURE__ */ (0, import_jsx_runtime6.jsxs)(
      "span",
      {
        className: "pg-mini-status st-ready",
        title: "Submittable \u2014 all approvals & presubmits satisfied",
        children: [
          /* @__PURE__ */ (0, import_jsx_runtime6.jsx)("span", { className: "pg-status-full", children: "Ready" }),
          /* @__PURE__ */ (0, import_jsx_runtime6.jsx)("span", { className: "pg-status-mini", children: "Ready" })
        ]
      }
    ) : null;
  }
  function GroupCheckbox({
    nums,
    checkedCount,
    labelTitle
  }) {
    let cleanNums = (nums || []).map(Number).filter((n) => n > 0), csv = cleanNums.join(","), allChecked = cleanNums.length > 0 && checkedCount === cleanNums.length, tip = labelTitle || "Select or deselect all commits in this group";
    return /* @__PURE__ */ (0, import_jsx_runtime6.jsx)(
      "label",
      {
        className: "group-check-wrap",
        htmlOnClick: "event.stopPropagation()",
        title: tip,
        children: /* @__PURE__ */ (0, import_jsx_runtime6.jsx)(
          "input",
          {
            type: "checkbox",
            className: "group-select-checkbox",
            "data-group-cls": csv,
            checked: allChecked,
            htmlOnClick: `toggleGroupCheckbox([${csv}], event)`,
            "aria-label": tip
          }
        )
      }
    );
  }
  function ColHeaderRow({
    groupNums,
    checkedCount,
    groupTitle,
    whoLabel = "Owner",
    ageLabel = "Updated"
  }) {
    let cleanNums = (groupNums || []).map(Number).filter((n) => n > 0), hasSel = checkedCount > 0;
    return /* @__PURE__ */ (0, import_jsx_runtime6.jsxs)("div", { className: `pg-col-header-row${hasSel ? " has-selection" : ""}`, children: [
      /* @__PURE__ */ (0, import_jsx_runtime6.jsx)("span", { htmlStyle: "display:flex;align-items:center;justify-content:center;", children: cleanNums.length > 0 ? /* @__PURE__ */ (0, import_jsx_runtime6.jsx)(
        GroupCheckbox,
        {
          nums: cleanNums,
          checkedCount,
          labelTitle: groupTitle
        }
      ) : null }),
      /* @__PURE__ */ (0, import_jsx_runtime6.jsx)("span", { children: "Subject" }),
      /* @__PURE__ */ (0, import_jsx_runtime6.jsx)("span", { className: "pg-col-who", children: whoLabel }),
      /* @__PURE__ */ (0, import_jsx_runtime6.jsx)("span", { className: "pg-col-age pg-col-label-right", children: ageLabel }),
      /* @__PURE__ */ (0, import_jsx_runtime6.jsx)("span", { className: "pg-col-size pg-col-label-center", children: "Size" }),
      /* @__PURE__ */ (0, import_jsx_runtime6.jsx)("span", { className: "pg-col-label-center", title: "Code-Review", children: "CR" }),
      /* @__PURE__ */ (0, import_jsx_runtime6.jsx)("span", { className: "pg-col-label-center pg-col-v", title: "Verified", children: "V" }),
      /* @__PURE__ */ (0, import_jsx_runtime6.jsx)("span", { className: "pg-col-label-center", title: "Presubmit-Verified", children: "PV" })
    ] });
  }
  function QueueBatchIdleHint() {
    return /* @__PURE__ */ (0, import_jsx_runtime6.jsxs)(import_jsx_runtime6.Fragment, { children: [
      /* @__PURE__ */ (0, import_jsx_runtime6.jsx)("span", { className: "queue-batch-hint-full", children: "Check commits for batch actions" }),
      /* @__PURE__ */ (0, import_jsx_runtime6.jsx)("span", { className: "queue-batch-hint-compact", children: "Batch edit" })
    ] });
  }
  function QueueBatchSelectStrip({
    cleanNums,
    checkedInGroupCount,
    totalChecked
  }) {
    if (!cleanNums || cleanNums.length === 0)
      return null;
    let stripCls = totalChecked > 0 ? "queue-batch-strip has-checked" : "queue-batch-strip";
    return /* @__PURE__ */ (0, import_jsx_runtime6.jsxs)("div", { id: "queueBatchStrip", className: stripCls, children: [
      /* @__PURE__ */ (0, import_jsx_runtime6.jsxs)("span", { className: "queue-batch-left", children: [
        /* @__PURE__ */ (0, import_jsx_runtime6.jsx)(
          GroupCheckbox,
          {
            nums: cleanNums,
            checkedCount: checkedInGroupCount,
            labelTitle: `Select all ${cleanNums.length} visible commits`
          }
        ),
        /* @__PURE__ */ (0, import_jsx_runtime6.jsx)("span", { children: `Select All (${cleanNums.length})` })
      ] }),
      totalChecked > 0 ? /* @__PURE__ */ (0, import_jsx_runtime6.jsxs)("span", { id: "queueBatchSelectMeta", className: "queue-checked-pill", children: [
        /* @__PURE__ */ (0, import_jsx_runtime6.jsx)("span", { children: `${totalChecked} selected` }),
        /* @__PURE__ */ (0, import_jsx_runtime6.jsx)(
          "button",
          {
            type: "button",
            className: "pg-btn-flat pg-batch-btn-clear nudge-btn",
            htmlOnClick: "event.stopPropagation(); clearCheckedCls()",
            title: "Clear checked selections",
            children: "Clear \u2715"
          }
        )
      ] }) : /* @__PURE__ */ (0, import_jsx_runtime6.jsx)("span", { id: "queueBatchSelectMeta", htmlStyle: "color:var(--g-subtle);", children: /* @__PURE__ */ (0, import_jsx_runtime6.jsx)(QueueBatchIdleHint, {}) })
    ] });
  }
  function QueueFilterBanner({
    summary,
    authorFilter = "all",
    searchQuery = "",
    includeUngatedMatches = !1
  }) {
    if (!summary || !summary.filterActive)
      return null;
    let activeDesc = [];
    authorFilter && authorFilter !== "all" && activeDesc.push(`author:${authorFilter}`);
    let trimmedSearch = (searchQuery || "").trim();
    trimmedSearch && activeDesc.push(`"${trimmedSearch}"`);
    let labelStr = activeDesc.join(" \xB7 "), gatedCount = Number(summary.gatedCommitCount || 0), ungatedCount = Number(summary.ungatedCommitCount || 0), hiddenCount = Number(summary.hiddenByGatesCount || 0);
    return summary.autoBypassedGates && gatedCount === 0 && ungatedCount > 0 ? /* @__PURE__ */ (0, import_jsx_runtime6.jsxs)("div", { className: "queue-filter-banner", children: [
      /* @__PURE__ */ (0, import_jsx_runtime6.jsxs)("span", { children: [
        "\u2139\uFE0F Showing ",
        /* @__PURE__ */ (0, import_jsx_runtime6.jsx)("strong", { children: ungatedCount }),
        ` matching CL${ungatedCount === 1 ? "" : "s"} for `,
        /* @__PURE__ */ (0, import_jsx_runtime6.jsx)("strong", { children: labelStr }),
        " (including unverified / already +2'd CLs)."
      ] }),
      /* @__PURE__ */ (0, import_jsx_runtime6.jsx)(
        "button",
        {
          className: "nudge-btn",
          htmlOnClick: "clearQueueSearchAndAuthor()",
          children: "Clear Filter \u2715"
        }
      )
    ] }) : hiddenCount > 0 && !includeUngatedMatches ? /* @__PURE__ */ (0, import_jsx_runtime6.jsxs)("div", { className: "queue-filter-banner", children: [
      /* @__PURE__ */ (0, import_jsx_runtime6.jsxs)("span", { children: [
        "\u{1F50D} Showing ",
        /* @__PURE__ */ (0, import_jsx_runtime6.jsx)("strong", { children: gatedCount }),
        ` verified CL${gatedCount === 1 ? "" : "s"} for `,
        /* @__PURE__ */ (0, import_jsx_runtime6.jsx)("strong", { children: labelStr }),
        " \xB7 ",
        /* @__PURE__ */ (0, import_jsx_runtime6.jsx)("strong", { children: `+${hiddenCount} more` }),
        " hidden by active gates."
      ] }),
      /* @__PURE__ */ (0, import_jsx_runtime6.jsxs)("div", { htmlStyle: "display:flex;gap:6px;", children: [
        /* @__PURE__ */ (0, import_jsx_runtime6.jsx)(
          "button",
          {
            className: "nudge-btn",
            htmlOnClick: "toggleIncludeUngatedMatches()",
            children: `Show All (${ungatedCount})`
          }
        ),
        /* @__PURE__ */ (0, import_jsx_runtime6.jsx)(
          "button",
          {
            className: "nudge-btn",
            htmlOnClick: "clearQueueSearchAndAuthor()",
            children: "Clear \u2715"
          }
        )
      ] })
    ] }) : includeUngatedMatches && hiddenCount > 0 ? /* @__PURE__ */ (0, import_jsx_runtime6.jsxs)("div", { className: "queue-filter-banner", children: [
      /* @__PURE__ */ (0, import_jsx_runtime6.jsxs)("span", { children: [
        "\u{1F50D} Showing all ",
        /* @__PURE__ */ (0, import_jsx_runtime6.jsx)("strong", { children: ungatedCount }),
        " matching CLs for ",
        /* @__PURE__ */ (0, import_jsx_runtime6.jsx)("strong", { children: labelStr }),
        ` (including ${hiddenCount} ungated).`
      ] }),
      /* @__PURE__ */ (0, import_jsx_runtime6.jsxs)("div", { htmlStyle: "display:flex;gap:6px;", children: [
        /* @__PURE__ */ (0, import_jsx_runtime6.jsx)(
          "button",
          {
            className: "nudge-btn",
            htmlOnClick: "toggleIncludeUngatedMatches()",
            children: `Verified Only (${gatedCount})`
          }
        ),
        /* @__PURE__ */ (0, import_jsx_runtime6.jsx)(
          "button",
          {
            className: "nudge-btn",
            htmlOnClick: "clearQueueSearchAndAuthor()",
            children: "Clear \u2715"
          }
        )
      ] })
    ] }) : null;
  }
  function SidebarHydratingPill({
    hydrating = !1
  }) {
    return hydrating ? /* @__PURE__ */ (0, import_jsx_runtime6.jsxs)("div", { className: "sidebar-hydrating-pill", children: [
      /* @__PURE__ */ (0, import_jsx_runtime6.jsx)("span", { className: "pv-mini-spinner" }),
      /* @__PURE__ */ (0, import_jsx_runtime6.jsx)("span", { children: "Loading remaining commits in background\u2026" })
    ] }) : null;
  }
  function renderCommitSubmitButtonHtml(target, isBusy = !1) {
    return renderReactToHtml(
      /* @__PURE__ */ (0, import_jsx_runtime6.jsx)(CommitSubmitButton, { target, isBusy })
    );
  }
  function renderBatchSubmitButtonHtml(target, isBusy = !1) {
    return renderReactToHtml(
      /* @__PURE__ */ (0, import_jsx_runtime6.jsx)(BatchSubmitButton, { target, isBusy })
    );
  }
  function renderPgCrVoteCellHtml(change, reviewers = []) {
    return renderReactToHtml(
      /* @__PURE__ */ (0, import_jsx_runtime6.jsx)(CrVoteCell, { change, reviewers })
    );
  }
  function renderPgVVoteBodyHtml(display) {
    return renderReactToHtml(/* @__PURE__ */ (0, import_jsx_runtime6.jsx)(VVoteBody, { display }));
  }
  function renderPgVVoteCellHtml(display) {
    return renderReactToHtml(/* @__PURE__ */ (0, import_jsx_runtime6.jsx)(VVoteCell, { display }));
  }
  function renderPgPvVoteBodyHtml(display) {
    return renderReactToHtml(/* @__PURE__ */ (0, import_jsx_runtime6.jsx)(PvVoteBody, { display }));
  }
  function renderPgPvVoteCellHtml(clNumber, stackId, display) {
    return renderReactToHtml(
      /* @__PURE__ */ (0, import_jsx_runtime6.jsx)(PvVoteCell, { clNumber, stackId, display })
    );
  }
  function renderPgStatusTagHtml(flags) {
    return renderReactToHtml(/* @__PURE__ */ (0, import_jsx_runtime6.jsx)(DashboardStatusTag, { flags }));
  }
  function renderGroupCheckboxHtml(nums, checkedCount, labelTitle) {
    return renderReactToHtml(
      /* @__PURE__ */ (0, import_jsx_runtime6.jsx)(
        GroupCheckbox,
        {
          nums,
          checkedCount,
          labelTitle
        }
      )
    );
  }
  function renderPgColHeaderRowHtml(groupNums, checkedCount, groupTitle, whoLabel = "Owner", ageLabel = "Updated") {
    return renderReactToHtml(
      /* @__PURE__ */ (0, import_jsx_runtime6.jsx)(
        ColHeaderRow,
        {
          groupNums,
          checkedCount,
          groupTitle,
          whoLabel,
          ageLabel
        }
      )
    );
  }
  function buildQueueBatchIdleHintHtml() {
    return renderReactToHtml(/* @__PURE__ */ (0, import_jsx_runtime6.jsx)(QueueBatchIdleHint, {}));
  }
  function renderQueueBatchSelectStripHtml(cleanNums, checkedInGroupCount, totalChecked) {
    return renderReactToHtml(
      /* @__PURE__ */ (0, import_jsx_runtime6.jsx)(
        QueueBatchSelectStrip,
        {
          cleanNums,
          checkedInGroupCount,
          totalChecked
        }
      )
    );
  }
  function renderQueueFilterBannerHtml(summary, authorFilter = "all", searchQuery = "", includeUngatedMatches = !1) {
    return renderReactToHtml(
      /* @__PURE__ */ (0, import_jsx_runtime6.jsx)(
        QueueFilterBanner,
        {
          summary,
          authorFilter,
          searchQuery,
          includeUngatedMatches
        }
      )
    );
  }
  function renderSidebarHydratingPillHtml(hydrating = !1) {
    return renderReactToHtml(/* @__PURE__ */ (0, import_jsx_runtime6.jsx)(SidebarHydratingPill, { hydrating }));
  }

  // ../../../../../../../../execroot/_main/bazel-out/k8-fastbuild-ST-f481a530da89/bin/external/aemu+/tools/emu-dev-cli/src/ui/gerrit/components/RebaseNoticeBanner.tsx
  var import_jsx_runtime7 = __toESM(require_jsx_runtime());
  function buildConflictBannerSpec(num, change, rebaseStatus) {
    let conflictList = Array.isArray(rebaseStatus.conflicts) && rebaseStatus.conflicts.length > 0 ? ` (${rebaseStatus.conflicts.join(", ")})` : "";
    return {
      bannerClass: "is-conflict",
      icon: "\u26A0\uFE0F",
      title: "Merge Conflict",
      desc: `This change cannot be merged cleanly onto ${change.branch || DEFAULT_TARGET_BRANCH_LABEL} due to merge conflicts${conflictList}.`,
      actions: /* @__PURE__ */ (0, import_jsx_runtime7.jsxs)(import_jsx_runtime7.Fragment, { children: [
        /* @__PURE__ */ (0, import_jsx_runtime7.jsx)(
          "button",
          {
            type: "button",
            id: `btnRebaseBannerResolve-${num}`,
            className: "pg-btn-rebase-banner",
            htmlOnClick: `openPgRebaseDialog(${num})`,
            children: "\u26A1 Rebase Change"
          }
        ),
        /* @__PURE__ */ (0, import_jsx_runtime7.jsx)(
          "button",
          {
            type: "button",
            id: `btnRebaseBannerAgent-${num}`,
            className: "pg-btn-rebase-banner-agent",
            htmlOnClick: `triggerHarnessForCl(${num}, 'resolve-conflicts')`,
            children: "\u{1F916} Fix with Agent"
          }
        ),
        /* @__PURE__ */ (0, import_jsx_runtime7.jsx)(
          "button",
          {
            type: "button",
            id: `btnRebaseBannerCheck-${num}`,
            className: "pg-btn-rebase-banner-opt",
            htmlOnClick: `refreshPgMergeableStatus(${num})`,
            title: "Re-query Gerrit for mergeability status",
            children: "\u{1F504} Recheck"
          }
        )
      ] })
    };
  }
  function buildParentMergedBannerSpec(num, change, rebaseStatus) {
    let branchName = change.branch || DEFAULT_BRANCH_NAME;
    return {
      bannerClass: "is-parent-merged",
      icon: "\u2139\uFE0F",
      title: "Parent Merged",
      desc: rebaseStatus.parentClNumber ? `Parent change ag/${rebaseStatus.parentClNumber} was merged into ${branchName}.` : rebaseStatus.reason || `Parent commit was merged into ${branchName}.`,
      actions: /* @__PURE__ */ (0, import_jsx_runtime7.jsxs)(import_jsx_runtime7.Fragment, { children: [
        /* @__PURE__ */ (0, import_jsx_runtime7.jsx)(
          "button",
          {
            type: "button",
            id: `btnRebaseBannerMerged-${num}`,
            className: "pg-btn-rebase-banner",
            htmlOnClick: `quickRebaseOnParent(${num}, null)`,
            children: `\u26A1 Rebase on ${branchName}`
          }
        ),
        /* @__PURE__ */ (0, import_jsx_runtime7.jsx)(
          "button",
          {
            type: "button",
            id: `btnRebaseBannerOpt-${num}`,
            className: "pg-btn-rebase-banner-opt",
            htmlOnClick: `openPgRebaseDialog(${num})`,
            children: "\u25BE Options\u2026"
          }
        )
      ] })
    };
  }
  function buildOutdatedBannerSpec(num, change, rebaseStatus) {
    let title = rebaseStatus.isIndirectlyOutdated ? "Relation Chain Outdated" : "Not Based on Latest Parent", desc = rebaseStatus.reason || (rebaseStatus.parentClNumber ? `Parent change ag/${rebaseStatus.parentClNumber} has updated patchset PS ${rebaseStatus.parentCurrentPs}.` : "Change is not based on latest branch commits."), btnText = rebaseStatus.parentClNumber ? `\u26A1 Rebase on ag/${rebaseStatus.parentClNumber} (PS ${rebaseStatus.parentCurrentPs})` : rebaseStatus.isIndirectlyOutdated ? "\u26A1 Rebase Stack Chain" : `\u26A1 Rebase on ${change.branch || DEFAULT_BASE_LABEL}`, parentArg = rebaseStatus.parentClNumber ? rebaseStatus.parentClNumber : "null", rebaseAction = rebaseStatus.isIndirectlyOutdated ? `quickRebaseStackChain(${num})` : `quickRebaseOnParent(${num}, ${parentArg})`;
    return {
      bannerClass: "is-outdated",
      icon: "\u26A0\uFE0F",
      title,
      desc,
      actions: /* @__PURE__ */ (0, import_jsx_runtime7.jsxs)(import_jsx_runtime7.Fragment, { children: [
        /* @__PURE__ */ (0, import_jsx_runtime7.jsx)(
          "button",
          {
            type: "button",
            id: `btnRebaseBannerOutdated-${num}`,
            className: "pg-btn-rebase-banner",
            htmlOnClick: rebaseAction,
            children: btnText
          }
        ),
        /* @__PURE__ */ (0, import_jsx_runtime7.jsx)(
          "button",
          {
            type: "button",
            id: `btnRebaseBannerOpt-${num}`,
            className: "pg-btn-rebase-banner-opt",
            htmlOnClick: `openPgRebaseDialog(${num})`,
            children: "\u25BE Options\u2026"
          }
        )
      ] })
    };
  }
  function resolveBannerSpec(num, change, rebaseStatus) {
    return rebaseStatus.hasConflict ? buildConflictBannerSpec(num, change, rebaseStatus) : rebaseStatus.isParentMerged ? buildParentMergedBannerSpec(num, change, rebaseStatus) : rebaseStatus.isOutdated ? buildOutdatedBannerSpec(num, change, rebaseStatus) : {
      bannerClass: "is-outdated",
      icon: "\u26A0\uFE0F",
      title: "Outdated Base",
      desc: rebaseStatus.reason || "Change requires rebase",
      actions: null
    };
  }
  function RebaseNoticeBanner({
    change,
    bundle = null,
    bridge
  }) {
    if (!change)
      return null;
    let rebaseStatus = (bridge ?? createHostBridge()).getChangeRebaseStatus(change, bundle);
    if (!rebaseStatus || !rebaseStatus.needsRebase)
      return null;
    let num = Number(change.number), spec = resolveBannerSpec(num, change, rebaseStatus);
    return /* @__PURE__ */ (0, import_jsx_runtime7.jsxs)(
      "div",
      {
        id: `pgRebaseNoticeBanner-${num}`,
        className: `pg-rebase-needed-banner ${spec.bannerClass}`,
        role: "alert",
        children: [
          /* @__PURE__ */ (0, import_jsx_runtime7.jsxs)("div", { className: "pg-rebase-banner-content", children: [
            /* @__PURE__ */ (0, import_jsx_runtime7.jsx)("span", { className: "pg-rebase-banner-icon", "aria-hidden": "true", children: spec.icon }),
            /* @__PURE__ */ (0, import_jsx_runtime7.jsxs)("div", { className: "pg-rebase-banner-text", children: [
              /* @__PURE__ */ (0, import_jsx_runtime7.jsx)("strong", { className: "pg-rebase-banner-title", children: `${spec.title}:` }),
              /* @__PURE__ */ (0, import_jsx_runtime7.jsx)("span", { className: "pg-rebase-banner-desc", children: spec.desc })
            ] })
          ] }),
          /* @__PURE__ */ (0, import_jsx_runtime7.jsx)("div", { className: "pg-rebase-banner-actions", children: spec.actions })
        ]
      }
    );
  }
  function renderPgRebaseNoticeBannerHtml(change, bundle = null, bridge) {
    return renderReactToHtml(
      /* @__PURE__ */ (0, import_jsx_runtime7.jsx)(RebaseNoticeBanner, { change, bundle, bridge })
    );
  }

  // ../../../../../../../../execroot/_main/bazel-out/k8-fastbuild-ST-f481a530da89/bin/external/aemu+/tools/emu-dev-cli/src/ui/gerrit/components/RelationBaseRow.tsx
  var import_jsx_runtime8 = __toESM(require_jsx_runtime());
  function resolveFirstParentEntrySha(baseCommit) {
    if (!baseCommit || !Array.isArray(baseCommit.parents) || !baseCommit.parents[0])
      return "";
    let firstParent = baseCommit.parents[0];
    return typeof firstParent == "string" ? firstParent : String(firstParent.commit || firstParent.sha || "");
  }
  function resolveParentCommitSha(change, baseCommit) {
    let rawSha = baseCommit?.parent_revision || baseCommit?.parent_commit || resolveFirstParentEntrySha(baseCommit) || change.parent_revision || change.parent_commit || "";
    return String(rawSha).trim();
  }
  function RelationBaseRow({
    change,
    stack,
    bridge
  }) {
    if (!change || !stack || !Array.isArray(stack.commits) || stack.commits.length === 0)
      return null;
    let activeBridge = bridge ?? createHostBridge(), baseCommit = stack.commits[0], targetBranch = String(
      baseCommit?.branch || change.branch || DEFAULT_BRANCH_NAME
    ).trim(), branchDisplay = activeBridge.getBranchDisplay(targetBranch), parentSha = resolveParentCommitSha(change, baseCommit), shortSha = parentSha ? parentSha.slice(0, 8) : "", isBaseMerged = isChangeMerged(baseCommit) || isChangeMerged(change), escapedBranch = escapeHtml(targetBranch), escapedSha = escapeHtml(parentSha), ariaLabel = `Target branch: ${targetBranch}${shortSha ? ` (Base: ${shortSha})` : ""}`, descText = isBaseMerged ? `Merged into ${branchDisplay}` : `Base target branch: ${branchDisplay}${shortSha ? ` (${shortSha})` : ""}`;
    return /* @__PURE__ */ (0, import_jsx_runtime8.jsxs)(
      "div",
      {
        className: "pg-relation-item pg-rel-base-row",
        tabIndex: 0,
        role: "link",
        "aria-label": ariaLabel,
        htmlOnClick: `filterDashboardByBranch('${escapedBranch}')`,
        htmlOnKeyDown: `onPgRelationBaseItemKeyDown(event, '${escapedBranch}', '${escapedSha}')`,
        title: `Target branch: ${targetBranch} \xB7 Click to filter dashboard`,
        children: [
          /* @__PURE__ */ (0, import_jsx_runtime8.jsx)("span", { className: "pg-relation-arrow", "aria-hidden": "true" }),
          /* @__PURE__ */ (0, import_jsx_runtime8.jsxs)("div", { className: "pg-rel-tree-col is-base is-last", children: [
            /* @__PURE__ */ (0, import_jsx_runtime8.jsx)("div", { className: "pg-rel-tree-line" }),
            /* @__PURE__ */ (0, import_jsx_runtime8.jsx)(
              "div",
              {
                className: "pg-rel-node-dot dot-base",
                title: `Target branch: ${targetBranch}`
              }
            )
          ] }),
          /* @__PURE__ */ (0, import_jsx_runtime8.jsxs)("div", { className: "pg-rel-item-body", children: [
            /* @__PURE__ */ (0, import_jsx_runtime8.jsxs)("div", { className: "pg-rel-item-meta", children: [
              /* @__PURE__ */ (0, import_jsx_runtime8.jsx)("span", { className: "pg-rel-base-tag", children: "BASE" }),
              /* @__PURE__ */ (0, import_jsx_runtime8.jsx)(
                "span",
                {
                  className: "pg-rel-branch-badge is-base-branch",
                  title: `Destination branch: ${targetBranch}`,
                  children: branchDisplay
                }
              ),
              shortSha ? /* @__PURE__ */ (0, import_jsx_runtime8.jsx)("span", { className: "pg-rel-base-sha", title: `Parent commit: ${parentSha}`, children: /* @__PURE__ */ (0, import_jsx_runtime8.jsx)("code", { children: shortSha }) }) : null,
              isBaseMerged ? /* @__PURE__ */ (0, import_jsx_runtime8.jsx)(
                "span",
                {
                  className: "pg-status-tag merged",
                  title: `Change or base merged into ${branchDisplay}`,
                  children: "MERGED"
                }
              ) : null
            ] }),
            /* @__PURE__ */ (0, import_jsx_runtime8.jsxs)("div", { className: "pg-rel-base-sub", children: [
              /* @__PURE__ */ (0, import_jsx_runtime8.jsx)("span", { className: "pg-relation-subj pg-rel-base-desc", children: descText }),
              /* @__PURE__ */ (0, import_jsx_runtime8.jsxs)("div", { className: "pg-rel-base-actions", children: [
                /* @__PURE__ */ (0, import_jsx_runtime8.jsx)(
                  "button",
                  {
                    type: "button",
                    id: `btnFilterBranch-${change.number}`,
                    className: "pg-btn-subtle-rel pg-btn-filter-branch",
                    htmlOnClick: `event.stopPropagation(); filterDashboardByBranch('${escapedBranch}')`,
                    title: `Filter dashboard by branch '${targetBranch}'`,
                    children: "\u{1F50D} Branch"
                  }
                ),
                /* @__PURE__ */ (0, import_jsx_runtime8.jsx)(
                  "button",
                  {
                    type: "button",
                    id: `btnCopyBranch-${change.number}`,
                    className: "pg-btn-subtle-rel pg-btn-copy-branch",
                    htmlOnClick: `event.stopPropagation(); copyPgBranch('${escapedBranch}')`,
                    title: `Copy branch name '${targetBranch}'`,
                    children: "\u{1F4CB} Branch"
                  }
                ),
                parentSha ? /* @__PURE__ */ (0, import_jsx_runtime8.jsx)(
                  "button",
                  {
                    type: "button",
                    id: `btnCopyBaseSha-${change.number}`,
                    className: "pg-btn-subtle-rel pg-btn-copy-base-sha",
                    htmlOnClick: `event.stopPropagation(); copyPgBaseCommitSha('${escapedSha}')`,
                    title: `Copy base parent commit SHA '${parentSha}'`,
                    children: "\u{1F4CB} Base SHA"
                  }
                ) : null
              ] })
            ] })
          ] })
        ]
      }
    );
  }
  function renderPgRelBaseRowHtml(change, stack, bridge) {
    return renderReactToHtml(
      /* @__PURE__ */ (0, import_jsx_runtime8.jsx)(RelationBaseRow, { change, stack, bridge })
    );
  }

  // ../../../../../../../../execroot/_main/bazel-out/k8-fastbuild-ST-f481a530da89/bin/external/aemu+/tools/emu-dev-cli/src/ui/gerrit/components/RelationStatusBadges.tsx
  var import_jsx_runtime9 = __toESM(require_jsx_runtime()), DEFAULT_OUTDATED_REASON = "Change is based on an older patchset of its parent \u2014 rebase recommended";
  function summarizeHumanReviewerVotes(reviewers) {
    let minVote = 0, hasCr1 = !1;
    for (let i = 0; i < reviewers.length; i++) {
      let reviewer = reviewers[i];
      if (!reviewer || reviewer.is_bot) continue;
      let vote = Number(reviewer.cr_vote || 0);
      vote < minVote && (minVote = vote), vote === 1 && (hasCr1 = !0);
    }
    return { minVote, hasCr1 };
  }
  function RelationStatusTag({
    peer,
    bridge,
    isConflict: isConflictProp,
    rebaseStatus: rebaseStatusProp
  }) {
    if (!peer)
      return null;
    let activeBridge = bridge ?? createHostBridge();
    if (isChangeMerged(peer))
      return /* @__PURE__ */ (0, import_jsx_runtime9.jsx)(
        "span",
        {
          className: "pg-status-tag merged",
          title: "Change has merged into target branch",
          children: "MERGED"
        }
      );
    if (isChangeAbandoned(peer))
      return /* @__PURE__ */ (0, import_jsx_runtime9.jsx)("span", { className: "pg-status-tag abandoned", title: "Change is abandoned", children: "ABANDONED" });
    if (isConflictProp !== void 0 ? isConflictProp : activeBridge.isClConflict(peer))
      return /* @__PURE__ */ (0, import_jsx_runtime9.jsx)(
        "span",
        {
          className: "pg-status-tag conflict",
          title: "Merge conflict with target branch \u2014 rebase required",
          children: "CONFLICT"
        }
      );
    let rebaseStatus = rebaseStatusProp !== void 0 ? rebaseStatusProp : activeBridge.getChangeRebaseStatus(peer);
    if (rebaseStatus && rebaseStatus.isOutdated) {
      let tagText = rebaseStatus.isIndirectlyOutdated ? "INDIRECT" : "NOT CURRENT", tooltip = rebaseStatus.reason || DEFAULT_OUTDATED_REASON;
      return /* @__PURE__ */ (0, import_jsx_runtime9.jsx)("span", { className: "pg-status-tag not-current", title: tooltip, children: tagText });
    }
    return peer.work_in_progress ? /* @__PURE__ */ (0, import_jsx_runtime9.jsx)("span", { className: "pg-status-tag wip", title: "Work in progress", children: "WIP" }) : null;
  }
  function RelationVoteChip({
    peer,
    bridge
  }) {
    if (!peer)
      return null;
    let reviewers = (bridge ?? createHostBridge()).getFallbackReviewersForCl(peer), { minVote, hasCr1: hasReviewerCr1 } = summarizeHumanReviewerVotes(reviewers);
    if (minVote <= -2)
      return /* @__PURE__ */ (0, import_jsx_runtime9.jsx)("span", { className: "pg-vote-chip v-rej", title: "Code-Review -2 (Do not submit)", children: "-2" });
    if (minVote === -1)
      return /* @__PURE__ */ (0, import_jsx_runtime9.jsx)("span", { className: "pg-vote-chip v-disliked", title: "Code-Review -1", children: "-1" });
    let requiredCr2Count = Number(
      peer.required_cr2_count || (peer.is_agent_cl ? 2 : 1)
    ), doneCount = (Array.isArray(peer.human_cr2) ? peer.human_cr2 : []).length || Number(peer.human_cr2_count || 0);
    return doneCount >= requiredCr2Count && requiredCr2Count > 0 ? /* @__PURE__ */ (0, import_jsx_runtime9.jsx)("span", { className: "pg-vote-chip v-approved", title: "Code-Review +2 satisfied", children: "\u2713+2" }) : doneCount > 0 ? /* @__PURE__ */ (0, import_jsx_runtime9.jsx)(
      "span",
      {
        className: "pg-vote-chip v-rec",
        title: `Code-Review: ${doneCount}/${requiredCr2Count} human +2 approvals`,
        children: "+2\xB7\xBD"
      }
    ) : Array.isArray(peer.human_cr1) && peer.human_cr1.length > 0 || hasReviewerCr1 ? /* @__PURE__ */ (0, import_jsx_runtime9.jsx)("span", { className: "pg-vote-chip v-rec", title: "Code-Review +1", children: "+1" }) : null;
  }
  function renderPgRelStatusTag(peer, bridge) {
    return renderReactToHtml(/* @__PURE__ */ (0, import_jsx_runtime9.jsx)(RelationStatusTag, { peer, bridge }));
  }
  function renderPgRelVoteChip(peer, bridge) {
    return renderReactToHtml(/* @__PURE__ */ (0, import_jsx_runtime9.jsx)(RelationVoteChip, { peer, bridge }));
  }

  // ../../../../../../../../execroot/_main/bazel-out/k8-fastbuild-ST-f481a530da89/bin/external/aemu+/tools/emu-dev-cli/src/ui/gerrit/components/RelationCommitRow.tsx
  var import_jsx_runtime10 = __toESM(require_jsx_runtime()), SINGLE_PATCHSET_LIST = [1];
  function resolveSortedPatchsetNumbers(rawPatchSets, fallbackCount) {
    if (!Array.isArray(rawPatchSets) || rawPatchSets.length === 0) {
      if (fallbackCount <= 1)
        return SINGLE_PATCHSET_LIST;
      let seq = [];
      for (let i = 1; i <= fallbackCount; i++)
        seq.push(i);
      return seq;
    }
    if (rawPatchSets.length === 1) {
      let single = Number(rawPatchSets[0]);
      return single > 0 ? [single] : SINGLE_PATCHSET_LIST;
    }
    return Array.from(
      new Set(rawPatchSets.map(Number).filter((num) => num > 0))
    ).sort((a, b) => a - b);
  }
  function computePatchsetChipState(peer, pNum, bridge) {
    let peerSel = bridge.getSelectedPatchset(pNum) || {}, peerBundle = bridge.getDiffBundle(pNum), peerTarget = Number(
      peerSel.ps || peerBundle?.patch_set || peer.patch_set || 1
    ), peerBase = String(
      peerSel.base || peerBundle?.base_patch_set || PATCHSET_BASE_TOKEN
    ), isPeerInterdiff = peerBase !== PATCHSET_BASE_TOKEN && peerBase !== "0", fallbackCount = Math.max(1, Number(peerTarget || peer.patch_set || 1)), allPeerPs = resolveSortedPatchsetNumbers(peer.patch_sets, fallbackCount), maxPeerPs = allPeerPs.length ? allPeerPs[allPeerPs.length - 1] : 1, curPeerIdx = allPeerPs.indexOf(peerTarget), prevPeerPs = curPeerIdx > 0 ? allPeerPs[curPeerIdx - 1] : peerTarget - 1;
    return {
      peerTarget,
      peerBase,
      isPeerInterdiff,
      allPeerPs,
      maxPeerPs,
      prevPeerPs
    };
  }
  function resolveNodeDotClass(params) {
    return params.isCurrent ? "dot-current" : params.isMerged ? "dot-merged" : params.isAbandoned ? "dot-abandoned" : params.isConflict ? "dot-conflict" : params.isPeerOutdated ? "dot-outdated" : params.isSubmittable ? "dot-submittable" : params.isWip ? "dot-wip" : "dot-open";
  }
  function resolveCommitClickAction(params) {
    return params.mode === "cherrypicks" && params.isCurrent ? "" : params.isBatchMode ? `toggleClCheckbox(${params.pNum}, event)` : params.mode === "chain" ? params.isContinuous ? `scrollToStackCommit(${params.pNum})` : `selectStackAndCl('${escapeHtml(params.stackId || "")}', ${params.pNum})` : `selectCl(${params.pNum})`;
  }
  function ChainPatchsetControls({
    peer,
    pNum,
    bridge
  }) {
    let psState = computePatchsetChipState(peer, pNum, bridge), { peerTarget, peerBase, isPeerInterdiff, allPeerPs, maxPeerPs, prevPeerPs } = psState, latestSuffix = peerTarget === maxPeerPs ? " (latest)" : "";
    return /* @__PURE__ */ (0, import_jsx_runtime10.jsxs)(import_jsx_runtime10.Fragment, { children: [
      allPeerPs.length > 1 ? /* @__PURE__ */ (0, import_jsx_runtime10.jsx)(
        "button",
        {
          type: "button",
          className: "pg-rel-ps-badge is-clickable",
          htmlOnClick: `event.stopPropagation(); stepPgPatchset(${pNum}, 1)`,
          title: `Patchset ${peerTarget} of ${maxPeerPs}${latestSuffix}. Click to cycle patchset (or [ / ] to step)`,
          children: `PS${peerTarget}`
        }
      ) : /* @__PURE__ */ (0, import_jsx_runtime10.jsx)(
        "span",
        {
          className: "pg-rel-ps-badge",
          title: `Patchset ${peerTarget} of ${maxPeerPs}${latestSuffix} (ag/${pNum})`,
          children: `PS${peerTarget}`
        }
      ),
      peerTarget < maxPeerPs ? /* @__PURE__ */ (0, import_jsx_runtime10.jsx)(
        "button",
        {
          type: "button",
          id: `btnRelLatest-${pNum}`,
          className: "pg-btn-subtle-rel pg-rel-latest-chip",
          htmlOnClick: `event.stopPropagation(); selectPgPatchsetComparison(${pNum}, ${maxPeerPs}, 'base')`,
          title: `Jump to latest patchset PS ${maxPeerPs} for ag/${pNum}`,
          children: `\u2794 PS${maxPeerPs} (latest)`
        }
      ) : null,
      isPeerInterdiff ? /* @__PURE__ */ (0, import_jsx_runtime10.jsx)(
        "button",
        {
          type: "button",
          id: `btnRelInterdiff-${pNum}`,
          className: "pg-btn-subtle-rel pg-rel-interdiff-chip is-active",
          htmlOnClick: `event.stopPropagation(); resetPgPatchsetComparisonToBase(${pNum})`,
          title: `Comparing PS ${peerBase} \u2192 PS ${peerTarget} for ag/${pNum}. Click to reset to Base (Shortcut: b)`,
          children: `\u26A1 PS ${peerBase}\u2192${peerTarget}`
        }
      ) : allPeerPs.length > 1 && peerTarget > 1 ? /* @__PURE__ */ (0, import_jsx_runtime10.jsx)(
        "button",
        {
          type: "button",
          id: `btnRelInterdiff-${pNum}`,
          className: "pg-btn-subtle-rel pg-rel-interdiff-chip",
          htmlOnClick: `event.stopPropagation(); togglePgDiffPreviousPatchset(${pNum})`,
          title: `Compare PS ${prevPeerPs} \u2192 PS ${peerTarget} for ag/${pNum} (Shortcut: b)`,
          children: `\u26A1 vs PS ${prevPeerPs}`
        }
      ) : null
    ] });
  }
  function ChainRebaseButton({
    pNum,
    peerBranch,
    isCurrent,
    isMerged,
    isAbandoned,
    rebaseStatus
  }) {
    if (isMerged || isAbandoned || !rebaseStatus || !rebaseStatus.needsRebase && !isCurrent)
      return null;
    let parentArg = rebaseStatus.parentClNumber || "null", targetDesc = rebaseStatus.parentClNumber ? `on parent ag/${rebaseStatus.parentClNumber}` : `on ${peerBranch || "base branch"}`, btnCls = `pg-btn-subtle-rel pg-btn-rel-rebase${rebaseStatus.needsRebase ? " needs-rebase" : ""}`;
    return /* @__PURE__ */ (0, import_jsx_runtime10.jsx)(
      "button",
      {
        type: "button",
        className: btnCls,
        htmlOnClick: `onPgRelationRebaseClick(event, ${pNum}, ${parentArg})`,
        title: `Rebase change ag/${pNum} ${targetDesc} (Alt+Click to rebase instantly)`,
        children: "\u26A1 Rebase"
      }
    );
  }
  function RelationCommitRow({
    peer,
    currentClNum,
    currentBranch,
    currentProject,
    mode,
    bridge,
    stackId,
    isFirst = !1,
    isLast = !1,
    rebaseStatus: rebaseStatusProp
  }) {
    let pNum = Number(peer.number), isCurrent = pNum === currentClNum, isChainMode = mode === "chain", peerBranch = bridge.getBranchDisplay(peer.branch), isCrossBranch = !!(peerBranch && currentBranch && peerBranch !== currentBranch), peerProject = bridge.getProjectDisplay(peer.project), isCrossProject = !!(peerProject && currentProject && peerProject !== currentProject), rebaseStatus = rebaseStatusProp !== void 0 ? rebaseStatusProp : isChainMode ? bridge.getChangeRebaseStatus(peer) : null, isPeerOutdated = !!(isChainMode && rebaseStatus?.isOutdated), isMerged = isChangeMerged(peer), isAbandoned = isChangeAbandoned(peer), isConflict = bridge.isClConflict(peer), isSubmittable = bridge.isClSubmittable(peer), isChecked = bridge.isClChecked(pNum), isBatchMode = bridge.isBatchModeActive(), nodeClass = resolveNodeDotClass({
      isCurrent,
      isMerged,
      isAbandoned,
      isConflict,
      isPeerOutdated,
      isSubmittable,
      isWip: !!peer.work_in_progress
    }), itemClasses = "pg-relation-item" + (isCurrent ? " is-current" : "") + (isMerged ? " is-merged" : "") + (isAbandoned ? " is-abandoned" : "") + (isConflict ? " has-conflict" : "") + (isPeerOutdated ? " is-outdated" : "") + (isChecked ? " is-checked" : ""), clickAction = resolveCommitClickAction({
      mode,
      pNum,
      isCurrent,
      isBatchMode,
      isContinuous: bridge.isContinuousStackDiff(),
      stackId
    }), keyDownAction = isChainMode ? `onPgRelationItemKeyDown(event, ${pNum}, '${escapeHtml(stackId || "")}')` : `onPgRelationItemKeyDown(event, ${pNum})`, treeColClass = isChainMode ? "pg-rel-tree-col" + (isFirst ? " is-first" : "") + (isLast ? " is-last" : "") : "pg-rel-tree-col", subjectText = peer.subject || "";
    return /* @__PURE__ */ (0, import_jsx_runtime10.jsxs)(
      "div",
      {
        className: itemClasses,
        tabIndex: 0,
        role: "link",
        "aria-label": `${isCurrent ? "Current change: " : ""}ag/${pNum}: ${subjectText}`,
        "data-pnum": pNum,
        htmlOnClick: clickAction,
        htmlOnKeyDown: keyDownAction,
        title: `ag/${pNum}: ${subjectText}`,
        children: [
          /* @__PURE__ */ (0, import_jsx_runtime10.jsx)("span", { className: "pg-relation-arrow", "aria-hidden": "true", children: isCurrent ? "\u2794" : "" }),
          /* @__PURE__ */ (0, import_jsx_runtime10.jsxs)("div", { className: treeColClass, children: [
            isChainMode ? /* @__PURE__ */ (0, import_jsx_runtime10.jsx)("div", { className: "pg-rel-tree-line" }) : null,
            /* @__PURE__ */ (0, import_jsx_runtime10.jsx)("div", { className: `pg-rel-node-dot ${nodeClass}` })
          ] }),
          /* @__PURE__ */ (0, import_jsx_runtime10.jsxs)("div", { className: "pg-rel-item-body", children: [
            /* @__PURE__ */ (0, import_jsx_runtime10.jsxs)("div", { className: "pg-rel-item-meta", children: [
              isChecked ? /* @__PURE__ */ (0, import_jsx_runtime10.jsx)(
                "span",
                {
                  className: "pg-rel-checked-badge",
                  title: "Selected for Batch Edit (Press x to toggle)",
                  children: "\u2611"
                }
              ) : null,
              /* @__PURE__ */ (0, import_jsx_runtime10.jsx)("span", { className: "pg-rel-cl-num", children: `ag/${pNum}` }),
              isChainMode ? /* @__PURE__ */ (0, import_jsx_runtime10.jsx)(ChainPatchsetControls, { peer, pNum, bridge }) : null,
              peerBranch ? /* @__PURE__ */ (0, import_jsx_runtime10.jsx)(
                "span",
                {
                  className: `pg-rel-branch-badge${isCrossBranch ? " is-cross-branch" : ""}`,
                  title: `Branch: ${peer.branch || ""}${isCrossBranch ? " (different branch)" : ""}`,
                  children: peerBranch
                }
              ) : null,
              isCrossProject ? /* @__PURE__ */ (0, import_jsx_runtime10.jsx)(
                "span",
                {
                  className: "pg-rel-project-badge",
                  title: `Project: ${peer.project || ""}`,
                  children: peerProject
                }
              ) : null,
              /* @__PURE__ */ (0, import_jsx_runtime10.jsx)(
                RelationStatusTag,
                {
                  peer,
                  bridge,
                  isConflict,
                  rebaseStatus
                }
              ),
              /* @__PURE__ */ (0, import_jsx_runtime10.jsx)(RelationVoteChip, { peer, bridge }),
              isSubmittable ? /* @__PURE__ */ (0, import_jsx_runtime10.jsx)("span", { className: "pg-relation-submittable", title: "Ready to submit", children: "\u2713" }) : null,
              isChainMode ? /* @__PURE__ */ (0, import_jsx_runtime10.jsx)(
                ChainRebaseButton,
                {
                  pNum,
                  peerBranch,
                  isCurrent,
                  isMerged,
                  isAbandoned,
                  rebaseStatus
                }
              ) : null
            ] }),
            /* @__PURE__ */ (0, import_jsx_runtime10.jsx)("span", { className: "pg-relation-subj", children: subjectText })
          ] })
        ]
      }
    );
  }

  // ../../../../../../../../execroot/_main/bazel-out/k8-fastbuild-ST-f481a530da89/bin/external/aemu+/tools/emu-dev-cli/src/ui/gerrit/components/RelationChainSidebar.tsx
  var import_jsx_runtime11 = __toESM(require_jsx_runtime()), TAB_BUTTON_ID_PREFIX = {
    chain: "btnRelTabChain-",
    submitted: "btnRelTabSubmitted-",
    topic: "btnRelTabTopic-",
    cherrypicks: "btnRelTabCherrypicks-"
  };
  function buildAvailableTabs(params) {
    let tabs = [];
    return params.hasStack && tabs.push({ id: "chain", label: `Relation chain (${params.stackLen})` }), params.hasSubmittedTogether && tabs.push({
      id: "submitted",
      label: `Submitted together (${params.submittedLen})`
    }), params.hasTopic && tabs.push({ id: "topic", label: `Same topic (${params.topicLen})` }), params.hasCherryPicks && tabs.push({ id: "cherrypicks", label: `Cherry-picks (${params.cherryLen})` }), tabs;
  }
  function RelationTabsHeaderLeft({
    curNum,
    activeTab,
    availableTabs
  }) {
    return availableTabs.length > 1 ? /* @__PURE__ */ (0, import_jsx_runtime11.jsx)("div", { className: "pg-rel-tabs", children: availableTabs.map((tab) => /* @__PURE__ */ (0, import_jsx_runtime11.jsx)(
      "button",
      {
        id: `${TAB_BUTTON_ID_PREFIX[tab.id]}${curNum}`,
        type: "button",
        className: `pg-rel-tab${activeTab === tab.id ? " is-active" : ""}`,
        htmlOnClick: `setPgRelatedChangesTab(${curNum}, '${tab.id}')`,
        title: `View ${tab.label}`,
        children: /* @__PURE__ */ (0, import_jsx_runtime11.jsx)("span", { children: tab.label })
      },
      tab.id
    )) }) : availableTabs.length === 1 ? /* @__PURE__ */ (0, import_jsx_runtime11.jsx)("span", { className: "pg-rel-title", children: availableTabs[0].label }) : null;
  }
  function RelationHeaderActions({
    curNum,
    activeTab,
    hasStack,
    stack,
    isContinuous,
    bridge
  }) {
    let showChainControls = hasStack && activeTab === "chain" && stack !== null, commits = stack?.commits || [], stackId = stack?.stack_id || "", canInterdiff = showChainControls && bridge.canStackInterdiff(commits), isStackInterdiff = showChainControls && bridge.isStackInterdiffActive(commits, stackId);
    canInterdiff && !isStackInterdiff && stack && bridge.getPersistedStackInterdiff(stackId) && (bridge.ensureStackInterdiffRestored(stack), isStackInterdiff = !0);
    let curIdx = commits.findIndex((commit) => Number(commit.number) === curNum), canPrev = curIdx > 0, canNext = curIdx >= 0 && curIdx < commits.length - 1;
    return /* @__PURE__ */ (0, import_jsx_runtime11.jsxs)("div", { className: "pg-relation-head-right", children: [
      canInterdiff ? /* @__PURE__ */ (0, import_jsx_runtime11.jsxs)(
        "button",
        {
          id: `btnRelToggleStackInterdiff-${curNum}`,
          type: "button",
          className: `pg-btn-subtle-rel pg-btn-rel-interdiff${isStackInterdiff ? " is-active" : ""}`,
          htmlOnClick: `event.stopPropagation(); togglePgStackInterdiff('${escapeHtml(
            stackId
          )}')`,
          title: isStackInterdiff ? "Reset all stack commits to Base comparison (Alt+I)" : "Toggle stack interdiff (PS n-1 \u2192 PS n) across all multi-patchset commits in stack (Alt+I)",
          children: [
            /* @__PURE__ */ (0, import_jsx_runtime11.jsx)("span", { className: "pg-rel-interdiff-icon", "aria-hidden": "true", children: isStackInterdiff ? "\u2715" : "\u26A1" }),
            /* @__PURE__ */ (0, import_jsx_runtime11.jsx)("span", { children: isStackInterdiff ? "Reset Stack" : "Stack Interdiff" })
          ]
        }
      ) : null,
      showChainControls ? /* @__PURE__ */ (0, import_jsx_runtime11.jsxs)(
        "button",
        {
          id: "btnToggleContinuousStack",
          type: "button",
          className: `pg-btn-stack-mode${isContinuous ? " active" : ""}`,
          htmlOnClick: `toggleContinuousStackDiff(event, '${escapeHtml(stackId)}')`,
          title: "Toggle Continuous Stack Review mode (Shortcut: Shift+S)",
          children: [
            /* @__PURE__ */ (0, import_jsx_runtime11.jsx)("span", { className: "pg-stack-mode-icon", "aria-hidden": "true", children: isContinuous ? "\u{1F4DA}" : "\u{1F4C4}" }),
            /* @__PURE__ */ (0, import_jsx_runtime11.jsx)("span", { children: isContinuous ? "Whole Stack ON" : "Whole Stack" })
          ]
        }
      ) : null,
      showChainControls ? /* @__PURE__ */ (0, import_jsx_runtime11.jsxs)(
        "div",
        {
          className: "pg-rel-stepper-group",
          role: "group",
          "aria-label": "Relation chain navigation",
          children: [
            /* @__PURE__ */ (0, import_jsx_runtime11.jsx)(
              "button",
              {
                id: `btnRelPrevCommit-${curNum}`,
                type: "button",
                className: "pg-btn-subtle-rel pg-btn-rel-step",
                htmlOnClick: "goToPrevStackCommit()",
                disabled: !canPrev,
                title: "Previous change in relation chain (Shift+[)",
                children: "\u25C0"
              }
            ),
            /* @__PURE__ */ (0, import_jsx_runtime11.jsx)(
              "button",
              {
                id: `btnRelNextCommit-${curNum}`,
                type: "button",
                className: "pg-btn-subtle-rel pg-btn-rel-step",
                htmlOnClick: "goToNextStackCommit()",
                disabled: !canNext,
                title: "Next change in relation chain (Shift+])",
                children: "\u25B6"
              }
            )
          ]
        }
      ) : null,
      /* @__PURE__ */ (0, import_jsx_runtime11.jsx)(
        "button",
        {
          id: `btnCopyRelatedCls-${curNum}`,
          type: "button",
          className: "pg-btn-subtle-rel",
          htmlOnClick: `copyPgRelatedCls(${curNum}, '${activeTab}')`,
          title: "Copy change numbers to clipboard",
          children: /* @__PURE__ */ (0, import_jsx_runtime11.jsx)("span", { children: "\u{1F4CB} CLs" })
        }
      )
    ] });
  }
  function ChainTabContent({
    change,
    curNum,
    stack,
    currentBranch,
    currentProject,
    bridge
  }) {
    let topDownCommits = stack.commits.slice().reverse(), totalNodes = topDownCommits.length, hasBaseRow = stack.commits.length > 0, outdatedCount = 0, rebaseStatuses = topDownCommits.map((peer) => {
      let status = bridge.getChangeRebaseStatus(peer);
      return status?.needsRebase && outdatedCount++, status;
    });
    return /* @__PURE__ */ (0, import_jsx_runtime11.jsxs)(import_jsx_runtime11.Fragment, { children: [
      outdatedCount > 0 ? /* @__PURE__ */ (0, import_jsx_runtime11.jsxs)(
        "div",
        {
          id: `pgRelChainWarningBanner-${curNum}`,
          className: "pg-rel-chain-warning-banner",
          role: "alert",
          children: [
            /* @__PURE__ */ (0, import_jsx_runtime11.jsx)("span", { className: "pg-rel-banner-icon", "aria-hidden": "true", children: "\u26A0\uFE0F" }),
            /* @__PURE__ */ (0, import_jsx_runtime11.jsx)("span", { className: "pg-rel-banner-text", children: `${outdatedCount} ${outdatedCount === 1 ? "change requires" : "changes require"} rebase` }),
            /* @__PURE__ */ (0, import_jsx_runtime11.jsx)(
              "button",
              {
                id: `btnRelRebaseAll-${curNum}`,
                type: "button",
                className: "pg-btn-subtle-rel pg-btn-rel-rebase-all",
                htmlOnClick: `event.stopPropagation(); quickRebaseStackChain(${curNum})`,
                title: "Rebase entire relation stack chain",
                children: "\u26A1 Rebase Stack"
              }
            )
          ]
        }
      ) : null,
      /* @__PURE__ */ (0, import_jsx_runtime11.jsxs)("div", { className: "pg-relation-list pg-rel-tree-list", children: [
        topDownCommits.map((peer, idx) => /* @__PURE__ */ (0, import_jsx_runtime11.jsx)(
          RelationCommitRow,
          {
            peer,
            currentClNum: curNum,
            currentBranch,
            currentProject,
            mode: "chain",
            bridge,
            stackId: stack.stack_id,
            isFirst: idx === 0,
            isLast: !hasBaseRow && idx === totalNodes - 1,
            rebaseStatus: rebaseStatuses[idx]
          },
          peer.number
        )),
        /* @__PURE__ */ (0, import_jsx_runtime11.jsx)(RelationBaseRow, { change, stack, bridge })
      ] })
    ] });
  }
  function SubmittedTogetherTabContent({
    change,
    curNum,
    submittedPeers,
    currentBranch,
    currentProject,
    bridge
  }) {
    let totalSub = submittedPeers.length, readyPeers = submittedPeers.filter(
      (peer) => bridge.isClSubmittable(peer) && !bridge.isClConflict(peer) && !peer.work_in_progress
    ), allReady = readyPeers.length === totalSub, nonReadyCount = totalSub - readyPeers.length, topicName = String(change.topic || "").trim();
    return /* @__PURE__ */ (0, import_jsx_runtime11.jsxs)(import_jsx_runtime11.Fragment, { children: [
      allReady ? /* @__PURE__ */ (0, import_jsx_runtime11.jsxs)("div", { className: "pg-rel-submitted-banner is-ready", role: "status", children: [
        /* @__PURE__ */ (0, import_jsx_runtime11.jsx)("span", { className: "pg-rel-banner-icon", "aria-hidden": "true", children: "\u2713" }),
        /* @__PURE__ */ (0, import_jsx_runtime11.jsx)("span", { className: "pg-rel-banner-text", children: `All ${totalSub} changes are ready to be submitted together` })
      ] }) : /* @__PURE__ */ (0, import_jsx_runtime11.jsxs)("div", { className: "pg-rel-submitted-banner is-warning", role: "alert", children: [
        /* @__PURE__ */ (0, import_jsx_runtime11.jsx)("span", { className: "pg-rel-banner-icon", "aria-hidden": "true", children: "\u26A0\uFE0F" }),
        /* @__PURE__ */ (0, import_jsx_runtime11.jsx)("span", { className: "pg-rel-banner-text", children: `Not all changes can be submitted together (${nonReadyCount} ${nonReadyCount === 1 ? "change requires" : "changes require"} review, verification, or conflict resolution)` })
      ] }),
      /* @__PURE__ */ (0, import_jsx_runtime11.jsxs)("div", { className: "pg-rel-submitted-bar", children: [
        /* @__PURE__ */ (0, import_jsx_runtime11.jsxs)("div", { className: "pg-rel-submitted-info", children: [
          /* @__PURE__ */ (0, import_jsx_runtime11.jsxs)(
            "span",
            {
              className: "pg-rel-submitted-summary",
              title: `${readyPeers.length} of ${totalSub} changes submittable`,
              children: [
                "\u{1F4E6} ",
                /* @__PURE__ */ (0, import_jsx_runtime11.jsx)("strong", { children: `${readyPeers.length}/${totalSub}` }),
                " ready"
              ]
            }
          ),
          topicName ? /* @__PURE__ */ (0, import_jsx_runtime11.jsxs)("span", { className: "pg-rel-topic-name", title: `Topic: ${topicName}`, children: [
            "\u{1F3F7}\uFE0F ",
            /* @__PURE__ */ (0, import_jsx_runtime11.jsx)("strong", { children: topicName })
          ] }) : null
        ] }),
        /* @__PURE__ */ (0, import_jsx_runtime11.jsxs)("div", { className: "pg-rel-topic-actions", children: [
          /* @__PURE__ */ (0, import_jsx_runtime11.jsx)(
            "button",
            {
              id: `btnFilterSubmitted-${curNum}`,
              type: "button",
              className: "pg-btn-subtle-rel",
              htmlOnClick: `applyPgSubmittedTogetherFilter(${curNum})`,
              title: "Filter dashboard by submitted together changes",
              children: "\u{1F50D} Filter"
            }
          ),
          /* @__PURE__ */ (0, import_jsx_runtime11.jsx)(
            "button",
            {
              id: `btnCopySubmittedCls-${curNum}`,
              type: "button",
              className: "pg-btn-subtle-rel",
              htmlOnClick: `copyPgRelatedCls(${curNum}, 'submitted')`,
              title: "Copy submitted together change numbers",
              children: "\u{1F4CB} Copy"
            }
          )
        ] })
      ] }),
      /* @__PURE__ */ (0, import_jsx_runtime11.jsx)("div", { className: "pg-relation-list", children: submittedPeers.map((peer) => /* @__PURE__ */ (0, import_jsx_runtime11.jsx)(
        RelationCommitRow,
        {
          peer,
          currentClNum: curNum,
          currentBranch,
          currentProject,
          mode: "submitted",
          bridge
        },
        peer.number
      )) })
    ] });
  }
  function CherryPicksTabContent({
    change,
    curNum,
    cherryPeers,
    currentBranch,
    currentProject,
    bridge
  }) {
    let cid = bridge.getChangeId(change), escapedCid = escapeHtml(cid), cherryGroup = bridge.getCherryPickGroup(change), allCherryItems = cherryGroup && cherryGroup.length > 0 ? cherryGroup : cherryPeers;
    return /* @__PURE__ */ (0, import_jsx_runtime11.jsxs)(import_jsx_runtime11.Fragment, { children: [
      /* @__PURE__ */ (0, import_jsx_runtime11.jsxs)("div", { className: "pg-rel-cherrypick-bar", children: [
        /* @__PURE__ */ (0, import_jsx_runtime11.jsxs)("span", { className: "pg-rel-cherry-id", title: `Change-Id: ${cid}`, children: [
          "\u{1F352} ",
          /* @__PURE__ */ (0, import_jsx_runtime11.jsx)("code", { children: `${cid.slice(0, 12)}\u2026` })
        ] }),
        /* @__PURE__ */ (0, import_jsx_runtime11.jsxs)("div", { className: "pg-rel-topic-actions", children: [
          /* @__PURE__ */ (0, import_jsx_runtime11.jsx)(
            "button",
            {
              id: `btnRelCherryPick-${curNum}`,
              type: "button",
              className: "pg-btn-subtle-rel pg-btn-rel-cherry",
              htmlOnClick: `event.stopPropagation(); openPgCherryPickDialog(${curNum})`,
              title: `Cherry pick ag/${curNum} to another branch (Shift+C)`,
              children: "\u{1F352} Cherry-pick\u2026"
            }
          ),
          /* @__PURE__ */ (0, import_jsx_runtime11.jsx)(
            "button",
            {
              id: `btnFilterCherry-${curNum}`,
              type: "button",
              className: "pg-btn-subtle-rel",
              htmlOnClick: `applyPgChangeIdFilter('${escapedCid}')`,
              title: `Filter dashboard by Change-Id '${cid}'`,
              children: "\u{1F50D} Filter"
            }
          ),
          /* @__PURE__ */ (0, import_jsx_runtime11.jsx)(
            "button",
            {
              id: `btnCopyCherryId-${curNum}`,
              type: "button",
              className: "pg-btn-subtle-rel",
              htmlOnClick: `copyPgChangeId('${escapedCid}')`,
              title: "Copy Change-Id to clipboard",
              children: "\u{1F4CB} Copy"
            }
          )
        ] })
      ] }),
      /* @__PURE__ */ (0, import_jsx_runtime11.jsx)("div", { className: "pg-relation-list", children: allCherryItems.map((peer) => /* @__PURE__ */ (0, import_jsx_runtime11.jsx)(
        RelationCommitRow,
        {
          peer,
          currentClNum: curNum,
          currentBranch,
          currentProject,
          mode: "cherrypicks",
          bridge
        },
        peer.number
      )) })
    ] });
  }
  function TopicTabContent({
    change,
    curNum,
    topicPeers,
    currentBranch,
    currentProject,
    bridge
  }) {
    let topicName = String(change.topic || "").trim(), escapedTopic = escapeHtml(topicName);
    return /* @__PURE__ */ (0, import_jsx_runtime11.jsxs)(import_jsx_runtime11.Fragment, { children: [
      /* @__PURE__ */ (0, import_jsx_runtime11.jsxs)("div", { className: "pg-rel-topic-bar", children: [
        /* @__PURE__ */ (0, import_jsx_runtime11.jsxs)("span", { className: "pg-rel-topic-name", title: `Topic: ${topicName}`, children: [
          "\u{1F3F7}\uFE0F ",
          /* @__PURE__ */ (0, import_jsx_runtime11.jsx)("strong", { children: topicName })
        ] }),
        /* @__PURE__ */ (0, import_jsx_runtime11.jsxs)("div", { className: "pg-rel-topic-actions", children: [
          /* @__PURE__ */ (0, import_jsx_runtime11.jsx)(
            "button",
            {
              id: `btnFilterTopic-${curNum}`,
              type: "button",
              className: "pg-btn-subtle-rel",
              htmlOnClick: `applyPgTopicFilter('${escapedTopic}')`,
              title: `Filter dashboard by topic '${topicName}'`,
              children: "\u{1F50D} Filter"
            }
          ),
          /* @__PURE__ */ (0, import_jsx_runtime11.jsx)(
            "button",
            {
              id: `btnCopyTopic-${curNum}`,
              type: "button",
              className: "pg-btn-subtle-rel",
              htmlOnClick: `copyPgTopic('${escapedTopic}')`,
              title: "Copy topic name to clipboard",
              children: "\u{1F4CB} Copy"
            }
          )
        ] })
      ] }),
      /* @__PURE__ */ (0, import_jsx_runtime11.jsx)("div", { className: "pg-relation-list", children: topicPeers.map((peer) => /* @__PURE__ */ (0, import_jsx_runtime11.jsx)(
        RelationCommitRow,
        {
          peer,
          currentClNum: curNum,
          currentBranch,
          currentProject,
          mode: "topic",
          bridge
        },
        peer.number
      )) })
    ] });
  }
  function resolveRelationHintHtml(activeTab, hasStack, isContinuous, tabCount) {
    let baseHint = RELATION_HINT_TOPIC;
    return activeTab === "chain" && hasStack ? baseHint = isContinuous ? RELATION_HINT_CONTINUOUS_CHAIN : RELATION_HINT_SINGLE_CHAIN : activeTab === "submitted" ? baseHint = RELATION_HINT_SUBMITTED : activeTab === "cherrypicks" && (baseHint = RELATION_HINT_CHERRYPICKS), tabCount > 1 && baseHint ? `${baseHint}${RELATION_HINT_CYCLE_TABS_SUFFIX}` : baseHint;
  }
  function RelationChainSidebar({
    change,
    bridge
  }) {
    if (!change)
      return null;
    let activeBridge = bridge ?? createHostBridge(), curNum = Number(change.number), stack = activeBridge.findStackForClOrId(
      curNum,
      activeBridge.getSelectedStackId()
    ), hasStack = !!(stack && Array.isArray(stack.commits) && stack.commits.length > 1), topicPeers = activeBridge.getTopicPeers(change), hasTopic = topicPeers.length > 0, cherryPeers = activeBridge.getCherryPickPeers(change), hasCherryPicks = cherryPeers.length > 0, submittedPeers = activeBridge.getSubmittedTogetherPeers(change), hasSubmittedTogether = submittedPeers.length > 1;
    if (!hasStack && !hasSubmittedTogether && !hasTopic && !hasCherryPicks)
      return null;
    let activeTab = activeBridge.getRelatedChangesTab(curNum), isContinuous = activeBridge.isContinuousStackDiff(), currentBranch = activeBridge.getBranchDisplay(change.branch), currentProject = activeBridge.getProjectDisplay(change.project), availableTabs = buildAvailableTabs({
      hasStack,
      stackLen: stack?.commits.length || 0,
      hasSubmittedTogether,
      submittedLen: submittedPeers.length,
      hasTopic,
      topicLen: topicPeers.length,
      hasCherryPicks,
      cherryLen: cherryPeers.length
    }), hintHtml = resolveRelationHintHtml(
      activeTab,
      hasStack,
      isContinuous,
      availableTabs.length
    );
    return /* @__PURE__ */ (0, import_jsx_runtime11.jsxs)("div", { className: "pg-relation-box", children: [
      /* @__PURE__ */ (0, import_jsx_runtime11.jsxs)("div", { className: "pg-relation-head", children: [
        /* @__PURE__ */ (0, import_jsx_runtime11.jsx)("div", { className: "pg-relation-head-left", children: /* @__PURE__ */ (0, import_jsx_runtime11.jsx)(
          RelationTabsHeaderLeft,
          {
            curNum,
            activeTab,
            availableTabs
          }
        ) }),
        /* @__PURE__ */ (0, import_jsx_runtime11.jsx)(
          RelationHeaderActions,
          {
            curNum,
            activeTab,
            hasStack,
            stack,
            isContinuous,
            bridge: activeBridge
          }
        )
      ] }),
      activeTab === "chain" && hasStack && stack ? /* @__PURE__ */ (0, import_jsx_runtime11.jsx)(
        ChainTabContent,
        {
          change,
          curNum,
          stack,
          currentBranch,
          currentProject,
          bridge: activeBridge
        }
      ) : activeTab === "submitted" ? /* @__PURE__ */ (0, import_jsx_runtime11.jsx)(
        SubmittedTogetherTabContent,
        {
          change,
          curNum,
          submittedPeers,
          currentBranch,
          currentProject,
          bridge: activeBridge
        }
      ) : activeTab === "cherrypicks" ? /* @__PURE__ */ (0, import_jsx_runtime11.jsx)(
        CherryPicksTabContent,
        {
          change,
          curNum,
          cherryPeers,
          currentBranch,
          currentProject,
          bridge: activeBridge
        }
      ) : /* @__PURE__ */ (0, import_jsx_runtime11.jsx)(
        TopicTabContent,
        {
          change,
          curNum,
          topicPeers,
          currentBranch,
          currentProject,
          bridge: activeBridge
        }
      ),
      /* @__PURE__ */ (0, import_jsx_runtime11.jsx)(
        "div",
        {
          className: "pg-relation-hint",
          dangerouslySetInnerHTML: { __html: hintHtml }
        }
      )
    ] });
  }
  function renderPgRelationChainHtml(change, bridge) {
    return renderReactToHtml(
      /* @__PURE__ */ (0, import_jsx_runtime11.jsx)(RelationChainSidebar, { change, bridge })
    );
  }

  // ../../../../../../../../execroot/_main/bazel-out/k8-fastbuild-ST-f481a530da89/bin/external/aemu+/tools/emu-dev-cli/src/ui/gerrit/components/StackSequenceAndFilesDialogs.tsx
  var import_jsx_runtime12 = __toESM(require_jsx_runtime()), PERCENT_MULTIPLIER2 = 100;
  function StackSequenceHeroBanner({
    seq,
    stackId
  }) {
    let escapedStackId = escapeHtml(stackId);
    if (seq.all_ready)
      return /* @__PURE__ */ (0, import_jsx_runtime12.jsxs)(import_jsx_runtime12.Fragment, { children: [
        /* @__PURE__ */ (0, import_jsx_runtime12.jsxs)("div", { className: "pg-stack-seq-hero-left", children: [
          /* @__PURE__ */ (0, import_jsx_runtime12.jsx)("span", { className: "pg-stack-seq-hero-icon", "aria-hidden": "true", children: "\u{1F389}" }),
          /* @__PURE__ */ (0, import_jsx_runtime12.jsxs)("div", { children: [
            /* @__PURE__ */ (0, import_jsx_runtime12.jsx)("div", { className: "pg-stack-seq-hero-title", children: "Entire Stack Ready for Submission!" }),
            /* @__PURE__ */ (0, import_jsx_runtime12.jsx)("div", { className: "pg-stack-seq-hero-sub", children: `All ${seq.total_commits} commits satisfy Gerrit submit requirements and can be submitted sequentially from bottom to top.` })
          ] })
        ] }),
        /* @__PURE__ */ (0, import_jsx_runtime12.jsxs)(
          "button",
          {
            type: "button",
            className: "btn pg-btn-stack-action pg-btn-stack-approve",
            htmlOnClick: `approveStackForBatchEdit('${escapedStackId}')`,
            title: `Approve all ${seq.total_commits} commits with Code-Review +2`,
            children: [
              /* @__PURE__ */ (0, import_jsx_runtime12.jsx)("span", { className: "pg-btn-icon", children: "\u26A1" }),
              " +2 All Commits"
            ]
          }
        )
      ] });
    let bottleneckNum = seq.bottleneck_commit?.number ?? "";
    return /* @__PURE__ */ (0, import_jsx_runtime12.jsxs)(import_jsx_runtime12.Fragment, { children: [
      /* @__PURE__ */ (0, import_jsx_runtime12.jsxs)("div", { className: "pg-stack-seq-hero-left", children: [
        /* @__PURE__ */ (0, import_jsx_runtime12.jsx)("span", { className: "pg-stack-seq-hero-icon", "aria-hidden": "true", children: "\u26A0\uFE0F" }),
        /* @__PURE__ */ (0, import_jsx_runtime12.jsxs)("div", { children: [
          /* @__PURE__ */ (0, import_jsx_runtime12.jsx)("div", { className: "pg-stack-seq-hero-title", children: `Submission Blocked at Step ${seq.bottleneck_step} (ag/${bottleneckNum})` }),
          /* @__PURE__ */ (0, import_jsx_runtime12.jsxs)("div", { className: "pg-stack-seq-hero-sub", children: [
            "Next commit in sequence is blocked: ",
            /* @__PURE__ */ (0, import_jsx_runtime12.jsx)("strong", { children: seq.bottleneck_reason || "" }),
            ". Commits above cannot be submitted until this is resolved."
          ] })
        ] })
      ] }),
      seq.bottleneck_commit ? /* @__PURE__ */ (0, import_jsx_runtime12.jsxs)(
        "button",
        {
          type: "button",
          className: "btn pg-btn-stack-action",
          htmlOnClick: `scrollToStackCommit(${seq.bottleneck_commit.number}); closePgStackSequenceDialog();`,
          title: "Jump to blocking commit",
          children: [
            /* @__PURE__ */ (0, import_jsx_runtime12.jsx)("span", { className: "pg-btn-icon", children: "\u{1F441}\uFE0F" }),
            " Jump to Blocker"
          ]
        }
      ) : null
    ] });
  }
  function StackSequenceCheckpointsBar({
    seq
  }) {
    let pctRev = Math.round(
      seq.reviewed_commits / Math.max(1, seq.total_commits) * PERCENT_MULTIPLIER2
    ), bottleneckNum = seq.bottleneck_commit?.number ?? "";
    return /* @__PURE__ */ (0, import_jsx_runtime12.jsxs)(import_jsx_runtime12.Fragment, { children: [
      /* @__PURE__ */ (0, import_jsx_runtime12.jsxs)("div", { className: "pg-stack-seq-cp-item", children: [
        /* @__PURE__ */ (0, import_jsx_runtime12.jsx)("span", { className: "pg-stack-seq-cp-label", children: "Review Checkpoint:" }),
        /* @__PURE__ */ (0, import_jsx_runtime12.jsx)("span", { className: "pg-stack-seq-cp-val", children: `${seq.reviewed_commits}/${seq.total_commits} reviewed (${pctRev}%)` })
      ] }),
      /* @__PURE__ */ (0, import_jsx_runtime12.jsx)("span", { htmlStyle: "color:var(--g-border);", children: "\xB7" }),
      /* @__PURE__ */ (0, import_jsx_runtime12.jsxs)("div", { className: "pg-stack-seq-cp-item", children: [
        /* @__PURE__ */ (0, import_jsx_runtime12.jsx)("span", { className: "pg-stack-seq-cp-label", children: "Submit Checkpoint:" }),
        /* @__PURE__ */ (0, import_jsx_runtime12.jsx)("span", { className: "pg-stack-seq-cp-val", children: `${seq.submittable_commits}/${seq.total_commits} submittable (${seq.seq_ready_count} in sequence)` })
      ] }),
      /* @__PURE__ */ (0, import_jsx_runtime12.jsx)("span", { htmlStyle: "color:var(--g-border);", children: "\xB7" }),
      /* @__PURE__ */ (0, import_jsx_runtime12.jsxs)("div", { className: "pg-stack-seq-cp-item", children: [
        /* @__PURE__ */ (0, import_jsx_runtime12.jsx)("span", { className: "pg-stack-seq-cp-label", children: "Active Bottleneck:" }),
        /* @__PURE__ */ (0, import_jsx_runtime12.jsx)(
          "span",
          {
            className: "pg-stack-seq-cp-val",
            htmlStyle: seq.all_ready ? "color:var(--g-green);" : "color:var(--g-amber);",
            children: seq.all_ready ? "None (All Ready \u2713)" : `Step ${seq.bottleneck_step} (ag/${bottleneckNum})`
          }
        )
      ] })
    ] });
  }
  function SequenceRowStatusBadge({
    item
  }) {
    if (item.seq_status === "ready")
      return /* @__PURE__ */ (0, import_jsx_runtime12.jsx)("span", { className: "pg-stack-seq-step-badge is-ready", children: `\u2713 Step ${item.step} Ready` });
    if (item.seq_status === "blocked_current")
      return /* @__PURE__ */ (0, import_jsx_runtime12.jsx)("span", { className: "pg-stack-seq-step-badge is-blocked", children: `\u2715 Step ${item.step} Blocked: ${item.blocker_reason || ""}` });
    if (item.seq_status === "waiting_ancestor") {
      let ancNum = item.blocking_ancestor?.number ?? "";
      return /* @__PURE__ */ (0, import_jsx_runtime12.jsx)("span", { className: "pg-stack-seq-step-badge is-waiting", children: `\u23F3 Waiting on #${item.blocking_step} (ag/${ancNum})` });
    }
    return item.seq_status === "merged" ? /* @__PURE__ */ (0, import_jsx_runtime12.jsx)("span", { className: "pg-stack-seq-step-badge is-merged", children: "Merged" }) : null;
  }
  function StackSequenceTable({
    seq,
    selectedClNumber,
    stackId
  }) {
    let escapedStackId = escapeHtml(stackId);
    return /* @__PURE__ */ (0, import_jsx_runtime12.jsxs)("table", { className: "pg-stack-seq-table", children: [
      /* @__PURE__ */ (0, import_jsx_runtime12.jsx)("thead", { children: /* @__PURE__ */ (0, import_jsx_runtime12.jsxs)("tr", { children: [
        /* @__PURE__ */ (0, import_jsx_runtime12.jsx)("th", { htmlStyle: "width:40px;text-align:center;", children: "#" }),
        /* @__PURE__ */ (0, import_jsx_runtime12.jsx)("th", { children: "Commit & Subject" }),
        /* @__PURE__ */ (0, import_jsx_runtime12.jsx)("th", { htmlStyle: "width:110px;", children: "Review" }),
        /* @__PURE__ */ (0, import_jsx_runtime12.jsx)("th", { htmlStyle: "width:65px;", children: "CR" }),
        /* @__PURE__ */ (0, import_jsx_runtime12.jsx)("th", { htmlStyle: "width:75px;", children: "Verified" }),
        /* @__PURE__ */ (0, import_jsx_runtime12.jsx)("th", { children: "Submit Sequence Status" }),
        /* @__PURE__ */ (0, import_jsx_runtime12.jsx)("th", { htmlStyle: "width:75px;text-align:right;", children: "Action" })
      ] }) }),
      /* @__PURE__ */ (0, import_jsx_runtime12.jsx)("tbody", { children: seq.items.map((item) => {
        let isCur = Number(selectedClNumber) === item.number, numClass = item.seq_status === "ready" || item.seq_status === "merged" ? "is-ready" : item.seq_status === "blocked_current" ? "is-blocked" : "", crColor = item.cr_label.startsWith("+") ? "var(--g-green)" : item.cr_label.startsWith("-") ? "var(--g-red)" : "var(--g-muted)", vColor = item.v_label.includes("+") ? "var(--g-green)" : "var(--g-muted)";
        return /* @__PURE__ */ (0, import_jsx_runtime12.jsxs)(
          "tr",
          {
            className: `pg-stack-seq-row${isCur ? " is-current" : ""}`,
            children: [
              /* @__PURE__ */ (0, import_jsx_runtime12.jsx)("td", { htmlStyle: "width:40px;text-align:center;", children: /* @__PURE__ */ (0, import_jsx_runtime12.jsx)("span", { className: `pg-stack-seq-num-badge ${numClass}`, children: item.step }) }),
              /* @__PURE__ */ (0, import_jsx_runtime12.jsxs)("td", { children: [
                /* @__PURE__ */ (0, import_jsx_runtime12.jsxs)("div", { htmlStyle: "display:flex;align-items:center;gap:6px;font-weight:600;", children: [
                  /* @__PURE__ */ (0, import_jsx_runtime12.jsx)(
                    "a",
                    {
                      href: `#pgStackCommit-${item.number}`,
                      className: "pg-cl-link",
                      htmlOnClick: `event.preventDefault(); scrollToStackCommit(${item.number}); closePgStackSequenceDialog();`,
                      children: `ag/${item.number}`
                    }
                  ),
                  /* @__PURE__ */ (0, import_jsx_runtime12.jsx)("span", { className: "pg-branch-badge", children: item.branch })
                ] }),
                /* @__PURE__ */ (0, import_jsx_runtime12.jsx)(
                  "div",
                  {
                    htmlStyle: "font-size:11.5px;color:var(--g-text);margin-top:2px;",
                    title: item.subject,
                    children: item.subject
                  }
                ),
                /* @__PURE__ */ (0, import_jsx_runtime12.jsx)("div", { htmlStyle: "font-size:10.5px;color:var(--g-muted);margin-top:1px;", children: `Author: ${item.author}` })
              ] }),
              /* @__PURE__ */ (0, import_jsx_runtime12.jsx)("td", { htmlStyle: "width:110px;", children: /* @__PURE__ */ (0, import_jsx_runtime12.jsxs)(
                "button",
                {
                  type: "button",
                  className: `pg-btn-stack-reviewed${item.is_reviewed ? " is-reviewed" : ""}`,
                  htmlOnClick: `togglePgStackCommitReviewed(${item.number}); renderPgStackSequenceDialogBodyDom('${escapedStackId}');`,
                  title: "Toggle commit reviewed",
                  children: [
                    /* @__PURE__ */ (0, import_jsx_runtime12.jsx)("span", { className: "pg-btn-icon", children: item.is_reviewed ? "\u2713" : "\u25CB" }),
                    /* @__PURE__ */ (0, import_jsx_runtime12.jsx)("span", { className: "pg-btn-label", children: item.is_reviewed ? "Reviewed" : "Mark" })
                  ]
                }
              ) }),
              /* @__PURE__ */ (0, import_jsx_runtime12.jsx)(
                "td",
                {
                  htmlStyle: `width:65px;font-weight:600;font-size:11.5px;color:${crColor};`,
                  children: item.cr_label
                }
              ),
              /* @__PURE__ */ (0, import_jsx_runtime12.jsx)("td", { htmlStyle: `width:75px;font-size:11.5px;color:${vColor};`, children: item.v_label }),
              /* @__PURE__ */ (0, import_jsx_runtime12.jsx)("td", { children: /* @__PURE__ */ (0, import_jsx_runtime12.jsx)(SequenceRowStatusBadge, { item }) }),
              /* @__PURE__ */ (0, import_jsx_runtime12.jsx)("td", { htmlStyle: "width:75px;text-align:right;", children: /* @__PURE__ */ (0, import_jsx_runtime12.jsx)(
                "button",
                {
                  type: "button",
                  className: "btn pg-btn-subtle",
                  htmlOnClick: `scrollToStackCommit(${item.number}); closePgStackSequenceDialog();`,
                  title: "Jump to commit in diff view",
                  children: "\u{1F441}\uFE0F Inspect"
                }
              ) })
            ]
          },
          item.number
        );
      }) })
    ] });
  }
  function StackFilesExtensionChips({
    extensions,
    activeExt
  }) {
    return /* @__PURE__ */ (0, import_jsx_runtime12.jsx)(import_jsx_runtime12.Fragment, { children: extensions.map((entry) => {
      let isAct = activeExt === entry.ext;
      return /* @__PURE__ */ (0, import_jsx_runtime12.jsx)(
        "button",
        {
          type: "button",
          className: `pg-stack-ext-chip${isAct ? " is-active" : ""}`,
          htmlOnClick: `setPgStackFilesFilterExt('${escapeHtml(entry.ext)}')`,
          children: `${entry.ext} (${entry.count})`
        },
        entry.ext
      );
    }) });
  }
  function StackFilesTable({
    items,
    selectedClNumber,
    activeFilterText
  }) {
    return items.length === 0 ? /* @__PURE__ */ (0, import_jsx_runtime12.jsx)("div", { htmlStyle: "padding:24px;text-align:center;color:var(--g-muted);font-size:13px;", children: `No files match the active search filter "${activeFilterText}".` }) : /* @__PURE__ */ (0, import_jsx_runtime12.jsxs)("table", { className: "pg-stack-files-table", children: [
      /* @__PURE__ */ (0, import_jsx_runtime12.jsx)("thead", { children: /* @__PURE__ */ (0, import_jsx_runtime12.jsxs)("tr", { children: [
        /* @__PURE__ */ (0, import_jsx_runtime12.jsx)("th", { htmlStyle: "width:36px;text-align:center;", children: "Type" }),
        /* @__PURE__ */ (0, import_jsx_runtime12.jsx)("th", { children: "File Path" }),
        /* @__PURE__ */ (0, import_jsx_runtime12.jsx)("th", { htmlStyle: "width:110px;", children: "Lines Delta" }),
        /* @__PURE__ */ (0, import_jsx_runtime12.jsx)("th", { children: "Touched in Commits" }),
        /* @__PURE__ */ (0, import_jsx_runtime12.jsx)("th", { htmlStyle: "width:105px;text-align:right;", children: "Action" })
      ] }) }),
      /* @__PURE__ */ (0, import_jsx_runtime12.jsx)("tbody", { children: items.map((item) => {
        let statusUpper = item.status || "M", statusCls = `status-${statusUpper.toLowerCase()}`, firstCl = item.occurrences[0]?.number || selectedClNumber || 0, quotedFile = escapeJsQuote(item.file);
        return /* @__PURE__ */ (0, import_jsx_runtime12.jsxs)(
          "tr",
          {
            className: "pg-stack-files-row",
            "data-file": item.file,
            children: [
              /* @__PURE__ */ (0, import_jsx_runtime12.jsx)("td", { htmlStyle: "width:36px;text-align:center;", children: /* @__PURE__ */ (0, import_jsx_runtime12.jsx)("span", { className: `pg-file-status-badge ${statusCls}`, children: statusUpper }) }),
              /* @__PURE__ */ (0, import_jsx_runtime12.jsx)("td", { children: /* @__PURE__ */ (0, import_jsx_runtime12.jsxs)("div", { className: "pg-stack-file-path-wrap", children: [
                item.extension ? /* @__PURE__ */ (0, import_jsx_runtime12.jsx)(
                  "span",
                  {
                    className: "pg-file-ext-badge",
                    htmlStyle: "font-size:10px;padding:1px 5px;margin-right:6px;",
                    children: item.extension
                  }
                ) : null,
                /* @__PURE__ */ (0, import_jsx_runtime12.jsx)("span", { className: "pg-stack-file-path", title: item.file, children: item.file })
              ] }) }),
              /* @__PURE__ */ (0, import_jsx_runtime12.jsxs)("td", { htmlStyle: "width:110px;white-space:nowrap;", children: [
                /* @__PURE__ */ (0, import_jsx_runtime12.jsx)("span", { className: "pg-files-add", children: `+${item.additions}` }),
                /* @__PURE__ */ (0, import_jsx_runtime12.jsx)("span", { className: "pg-files-del", children: `-${item.deletions}` })
              ] }),
              /* @__PURE__ */ (0, import_jsx_runtime12.jsx)("td", { children: /* @__PURE__ */ (0, import_jsx_runtime12.jsx)("div", { className: "pg-stack-commits-list", children: item.occurrences.map((occ) => /* @__PURE__ */ (0, import_jsx_runtime12.jsx)(
                "button",
                {
                  type: "button",
                  className: "pg-stack-commit-chip",
                  htmlOnClick: `jumpToStackFileDiff(${occ.number}, '${quotedFile}')`,
                  title: `Step ${occ.step}: ag/${occ.number} (${occ.subject || ""}) \xB7 +${occ.additions}/-${occ.deletions}`,
                  children: /* @__PURE__ */ (0, import_jsx_runtime12.jsx)("span", { children: `Step ${occ.step} (ag/${occ.number})` })
                },
                `${occ.number}-${occ.step}`
              )) }) }),
              /* @__PURE__ */ (0, import_jsx_runtime12.jsx)("td", { htmlStyle: "width:105px;text-align:right;", children: /* @__PURE__ */ (0, import_jsx_runtime12.jsx)(
                "button",
                {
                  type: "button",
                  className: "btn pg-btn-subtle",
                  htmlOnClick: `jumpToStackFileDiff(${firstCl}, '${quotedFile}')`,
                  title: "Jump directly to file diff in stack",
                  children: "Jump to diff \u2197"
                }
              ) })
            ]
          },
          item.file
        );
      }) })
    ] });
  }
  function renderPgStackSequenceHeroHtml(seq, stackId = "") {
    return renderReactToHtml(
      /* @__PURE__ */ (0, import_jsx_runtime12.jsx)(StackSequenceHeroBanner, { seq, stackId })
    );
  }
  function renderPgStackSequenceCheckpointsHtml(seq) {
    return renderReactToHtml(/* @__PURE__ */ (0, import_jsx_runtime12.jsx)(StackSequenceCheckpointsBar, { seq }));
  }
  function renderPgStackSequenceTableHtml(seq, selectedClNumber, stackId = "") {
    return renderReactToHtml(
      /* @__PURE__ */ (0, import_jsx_runtime12.jsx)(
        StackSequenceTable,
        {
          seq,
          selectedClNumber,
          stackId
        }
      )
    );
  }
  function renderPgStackFilesExtChipsHtml(extensions, activeExt = "") {
    return renderReactToHtml(
      /* @__PURE__ */ (0, import_jsx_runtime12.jsx)(StackFilesExtensionChips, { extensions, activeExt })
    );
  }
  function renderPgStackFilesTableHtml(items, selectedClNumber, activeFilterText = "") {
    return renderReactToHtml(
      /* @__PURE__ */ (0, import_jsx_runtime12.jsx)(
        StackFilesTable,
        {
          items,
          selectedClNumber,
          activeFilterText
        }
      )
    );
  }

  // ../../../../../../../../execroot/_main/bazel-out/k8-fastbuild-ST-f481a530da89/bin/external/aemu+/tools/emu-dev-cli/src/ui/gerrit/components/StickyScrollActions.tsx
  var import_jsx_runtime13 = __toESM(require_jsx_runtime());
  function StickyScrollActions({
    clNum,
    draftCount = 0
  }) {
    let num = Number(clNum), count = Math.max(0, Number(draftCount || 0)), replyBtnLabel = count > 0 ? `Reply (${count} draft${count > 1 ? "s" : ""})` : "Reply", replyClass = `pg-btn-primary pg-reply-btn pg-sticky-scroll-only-btn btn-header-reply${count > 0 ? " has-drafts" : ""}`;
    return /* @__PURE__ */ (0, import_jsx_runtime13.jsxs)(import_jsx_runtime13.Fragment, { children: [
      /* @__PURE__ */ (0, import_jsx_runtime13.jsx)(
        "button",
        {
          id: `btnStickyHeaderReply-${num}`,
          type: "button",
          className: replyClass,
          htmlOnClick: `event.stopPropagation(); openPgHeaderReply(${num})`,
          title: `Reply and vote on ag/${num} (Shortcut: r)`,
          children: replyBtnLabel
        }
      ),
      /* @__PURE__ */ (0, import_jsx_runtime13.jsxs)(
        "button",
        {
          id: `btnStickyBackToTop-${num}`,
          type: "button",
          className: "btn pg-btn-header-action pg-sticky-scroll-only-btn btn-back-to-top",
          htmlOnClick: `event.stopPropagation(); scrollPgChangeToTop(${num})`,
          title: `Scroll back to top of change ag/${num}`,
          children: [
            /* @__PURE__ */ (0, import_jsx_runtime13.jsx)("span", { className: "pg-btn-icon", "aria-hidden": "true", children: "\u2191" }),
            " Top"
          ]
        }
      )
    ] });
  }
  function renderPgStickyScrollActionsHtml(clNum, draftCount = 0) {
    return renderReactToHtml(
      /* @__PURE__ */ (0, import_jsx_runtime13.jsx)(StickyScrollActions, { clNum, draftCount })
    );
  }

  // ../../../../../../../../execroot/_main/bazel-out/k8-fastbuild-ST-f481a530da89/bin/external/aemu+/tools/emu-dev-cli/src/ui/gerrit/index.tsx
  var import_jsx_runtime14 = __toESM(require_jsx_runtime());
  function mountPgRelationChainIsland(container, change, bridge) {
    mountReactIsland(
      container,
      /* @__PURE__ */ (0, import_jsx_runtime14.jsx)(RelationChainSidebar, { change, bridge })
    );
  }
  function mountPgRebaseNoticeBannerIsland(container, change, bundle = null, bridge) {
    mountReactIsland(
      container,
      /* @__PURE__ */ (0, import_jsx_runtime14.jsx)(RebaseNoticeBanner, { change, bundle, bridge })
    );
  }
  var AemuGerritReact = {
    React: import_react2.default,
    escapeHtml,
    escapeJsQuote,
    renderReactToHtml,
    mountReactIsland,
    unmountReactIsland,
    gerritUiStore,
    useGerritUIStore,
    createHostBridge,
    createEmptyRebaseStatus,
    formatBranchDisplay,
    isChangeMerged,
    isChangeAbandoned,
    RelationStatusTag,
    RelationVoteChip,
    RelationBaseRow,
    RelationCommitRow,
    RelationChainSidebar,
    RebaseNoticeBanner,
    StickyScrollActions,
    ContinuousStackBanner,
    StackCommitSequenceBar,
    StackSequenceHeroBanner,
    StackSequenceCheckpointsBar,
    StackSequenceTable,
    StackFilesExtensionChips,
    StackFilesTable,
    ExpandSidebarButton,
    CommitAemuMapButton,
    SrSummaryChip,
    CopyLinksMenu,
    HeaderActionButtons,
    ChangeHeaderTitleBar,
    CommitSubmitButton,
    BatchSubmitButton,
    CrVoteCell,
    VVoteBody,
    VVoteCell,
    PvVoteBody,
    PvVoteCell,
    DashboardStatusTag,
    GroupCheckbox,
    ColHeaderRow,
    QueueBatchIdleHint,
    QueueBatchSelectStrip,
    QueueFilterBanner,
    SidebarHydratingPill,
    AiAnalysisStatusBanner,
    AiAlternativeCard,
    CommitAiAnalysisPanel,
    ClSwarmBadge,
    SpecialistChips,
    SwarmFindingsList,
    CommitSwarmPanel,
    VoteBoxChip,
    SrAtomsList,
    SrActionBtn,
    SrDetailRow,
    CrSubmitRequirementRow,
    PvSubmitRequirementRow,
    TriggerVotesSection,
    SubmitRequirementsSection,
    SrAuditCard,
    PRESUBMIT_KIND_LABELS,
    PresubmitStatusPill,
    SinglePresubmitTargetRow,
    ChecksTabButton,
    ChecksControls,
    CheckRunLogDrawer,
    CheckDetailCard,
    ChecksView,
    SavedSearchesList,
    ReplyDialog,
    COMMIT_MSG_MAGIC_PATH,
    MERGE_LIST_MAGIC_PATH,
    CodeViewerSidebar,
    CodeViewerToggleButton,
    buildCodeViewerDirectoryTree,
    renderPgCodeViewerSidebarHtml,
    renderPgCodeViewerToggleButtonHtml,
    renderPgRelStatusTag,
    renderPgRelVoteChip,
    renderPgRelBaseRowHtml,
    renderPgRelationChainHtml,
    renderPgRebaseNoticeBannerHtml,
    renderPgStickyScrollActionsHtml,
    renderPgContinuousStackBannerHtml,
    renderPgStackCommitSequenceBarHtml,
    renderPgStackSequenceHeroHtml,
    renderPgStackSequenceCheckpointsHtml,
    renderPgStackSequenceTableHtml,
    renderPgStackFilesExtChipsHtml,
    renderPgStackFilesTableHtml,
    renderExpandSidebarButtonHtml,
    renderCommitAemuMapButtonHtml,
    renderPgSrSummaryChipHtml,
    renderPgCopyLinksMenuHtml,
    renderPgHeaderActionButtonsHtml,
    renderPgChangeHeaderTitleBarHtml,
    renderCommitSubmitButtonHtml,
    renderBatchSubmitButtonHtml,
    renderPgCrVoteCellHtml,
    renderPgVVoteBodyHtml,
    renderPgVVoteCellHtml,
    renderPgPvVoteBodyHtml,
    renderPgPvVoteCellHtml,
    renderPgStatusTagHtml,
    renderGroupCheckboxHtml,
    renderPgColHeaderRowHtml,
    buildQueueBatchIdleHintHtml,
    renderQueueBatchSelectStripHtml,
    renderQueueFilterBannerHtml,
    renderSidebarHydratingPillHtml,
    renderAiAnalysisStatusBannerHtml,
    renderAiAlternativeCardHtml,
    renderCommitAiAnalysisPanelHtml,
    renderClSwarmBadgeHtml,
    renderSpecialistChipsHtml,
    renderSwarmFindingsListHtml,
    renderCommitSwarmPanelHtml,
    renderPgVoteBoxChipHtml,
    renderPgSrAtomsHtml,
    renderPgSrActionBtnHtml,
    renderPgSrDetailRowHtml,
    renderPgCrSubmitRequirementRowHtml,
    renderPgPvSubmitRequirementRowHtml,
    renderPgTriggerVotesSectionHtml,
    renderPgSubmitRequirementsSectionHtml,
    renderPgSrAuditCardHtml,
    renderPresubmitStatusPillHtml,
    renderSinglePresubmitTargetRowHtml,
    renderPgChecksTabButtonHtml,
    renderPgChecksControlsHtml,
    renderPgCheckRunLogDrawerHtml,
    renderPgCheckDetailCardHtml,
    renderPgChecksViewComponentHtml,
    renderPgSavedSearchesCardsHtml,
    renderPgReplyDialogComponentHtml,
    mountPgRelationChainIsland,
    mountPgRebaseNoticeBannerIsland
  }, globalScope = globalThis;
  globalScope.AemuGerritReact = AemuGerritReact;
  typeof window < "u" && (window.AemuGerritReact = AemuGerritReact);
})();
/*! Bundled license information:

react/cjs/react.production.js:
  (**
   * @license React
   * react.production.js
   *
   * Copyright (c) Meta Platforms, Inc. and affiliates.
   *
   * This source code is licensed under the MIT license found in the
   * LICENSE file in the root directory of this source tree.
   *)

react/cjs/react-jsx-runtime.production.js:
  (**
   * @license React
   * react-jsx-runtime.production.js
   *
   * Copyright (c) Meta Platforms, Inc. and affiliates.
   *
   * This source code is licensed under the MIT license found in the
   * LICENSE file in the root directory of this source tree.
   *)
*/
