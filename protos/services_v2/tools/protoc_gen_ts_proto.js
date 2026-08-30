"use strict";
var __getOwnPropNames = Object.getOwnPropertyNames;
var __commonJS = (cb, mod) => function __require() {
  return mod || (0, cb[__getOwnPropNames(cb)[0]])((mod = { exports: {} }).exports, mod), mod.exports;
};

// hardware/generic/goldfish/emulator/ui/aquarium/node_modules/@bufbuild/protobuf/dist/commonjs/wire/varint.js
var require_varint = __commonJS({
  "hardware/generic/goldfish/emulator/ui/aquarium/node_modules/@bufbuild/protobuf/dist/commonjs/wire/varint.js"(exports2) {
    "use strict";
    Object.defineProperty(exports2, "__esModule", { value: true });
    exports2.varint64read = varint64read;
    exports2.varint64write = varint64write;
    exports2.int64FromString = int64FromString;
    exports2.int64ToString = int64ToString;
    exports2.uInt64ToString = uInt64ToString;
    exports2.varint32write = varint32write;
    exports2.varint32read = varint32read;
    function varint64read() {
      const buf = this.buf;
      let pos = this.pos;
      let lo = 0;
      let hi = 0;
      for (let shift = 0; shift < 28; shift += 7) {
        const b = buf[pos++];
        lo |= (b & 127) << shift;
        if ((b & 128) == 0) {
          this.pos = pos;
          this.assertBounds();
          this.varint64Lo = lo;
          this.varint64Hi = hi;
          return;
        }
      }
      const middleByte = buf[pos++];
      lo |= (middleByte & 15) << 28;
      hi = (middleByte & 112) >> 4;
      if ((middleByte & 128) == 0) {
        this.pos = pos;
        this.assertBounds();
        this.varint64Lo = lo;
        this.varint64Hi = hi;
        return;
      }
      for (let shift = 3; shift <= 31; shift += 7) {
        const b = buf[pos++];
        hi |= (b & 127) << shift;
        if ((b & 128) == 0) {
          this.pos = pos;
          this.assertBounds();
          this.varint64Lo = lo;
          this.varint64Hi = hi;
          return;
        }
      }
      throw new Error("invalid varint");
    }
    function varint64write(lo, hi, bytes) {
      for (let i = 0; i < 28; i = i + 7) {
        const shift = lo >>> i;
        const hasNext = !(shift >>> 7 == 0 && hi == 0);
        const byte = (hasNext ? shift | 128 : shift) & 255;
        bytes.push(byte);
        if (!hasNext) {
          return;
        }
      }
      const splitBits = lo >>> 28 & 15 | (hi & 7) << 4;
      const hasMoreBits = !(hi >> 3 == 0);
      bytes.push((hasMoreBits ? splitBits | 128 : splitBits) & 255);
      if (!hasMoreBits) {
        return;
      }
      for (let i = 3; i < 31; i = i + 7) {
        const shift = hi >>> i;
        const hasNext = !(shift >>> 7 == 0);
        const byte = (hasNext ? shift | 128 : shift) & 255;
        bytes.push(byte);
        if (!hasNext) {
          return;
        }
      }
      bytes.push(hi >>> 31 & 1);
    }
    var TWO_PWR_32_DBL = 4294967296;
    function int64FromString(dec) {
      const minus = dec[0] === "-";
      if (minus) {
        dec = dec.slice(1);
      }
      const base = 1e6;
      let lowBits = 0;
      let highBits = 0;
      function add1e6digit(begin, end) {
        const digit1e6 = Number(dec.slice(begin, end));
        highBits *= base;
        lowBits = lowBits * base + digit1e6;
        if (lowBits >= TWO_PWR_32_DBL) {
          highBits = highBits + (lowBits / TWO_PWR_32_DBL | 0);
          lowBits = lowBits % TWO_PWR_32_DBL;
        }
      }
      add1e6digit(-24, -18);
      add1e6digit(-18, -12);
      add1e6digit(-12, -6);
      add1e6digit(-6);
      return minus ? negate(lowBits, highBits) : newBits(lowBits, highBits);
    }
    function int64ToString(lo, hi) {
      let bits = newBits(lo, hi);
      const negative = bits.hi & 2147483648;
      if (negative) {
        bits = negate(bits.lo, bits.hi);
      }
      const result = uInt64ToString(bits.lo, bits.hi);
      return negative ? "-" + result : result;
    }
    function uInt64ToString(lo, hi) {
      ({ lo, hi } = toUnsigned(lo, hi));
      if (hi <= 2097151) {
        return String(TWO_PWR_32_DBL * hi + lo);
      }
      const low = lo & 16777215;
      const mid = (lo >>> 24 | hi << 8) & 16777215;
      const high = hi >> 16 & 65535;
      let digitA = low + mid * 6777216 + high * 6710656;
      let digitB = mid + high * 8147497;
      let digitC = high * 2;
      const base = 1e7;
      if (digitA >= base) {
        digitB += Math.floor(digitA / base);
        digitA %= base;
      }
      if (digitB >= base) {
        digitC += Math.floor(digitB / base);
        digitB %= base;
      }
      return digitC.toString() + decimalFrom1e7WithLeadingZeros(digitB) + decimalFrom1e7WithLeadingZeros(digitA);
    }
    function toUnsigned(lo, hi) {
      return { lo: lo >>> 0, hi: hi >>> 0 };
    }
    function newBits(lo, hi) {
      return { lo: lo | 0, hi: hi | 0 };
    }
    function negate(lowBits, highBits) {
      highBits = ~highBits;
      if (lowBits) {
        lowBits = ~lowBits + 1;
      } else {
        highBits += 1;
      }
      return newBits(lowBits, highBits);
    }
    var decimalFrom1e7WithLeadingZeros = (digit1e7) => {
      const partial = String(digit1e7);
      return "0000000".slice(partial.length) + partial;
    };
    function varint32write(value, bytes) {
      if (value >>> 0 < 128) {
        bytes.push(value);
        return;
      }
      if (value >= 0) {
        while (value > 127) {
          bytes.push(value & 127 | 128);
          value = value >>> 7;
        }
        bytes.push(value);
      } else {
        for (let i = 0; i < 9; i++) {
          bytes.push(value & 127 | 128);
          value = value >> 7;
        }
        bytes.push(1);
      }
    }
    function varint32read() {
      let b = this.buf[this.pos++];
      if ((b & 128) === 0) {
        this.assertBounds();
        return b;
      }
      let result = b & 127;
      b = this.buf[this.pos++];
      result |= (b & 127) << 7;
      if ((b & 128) === 0) {
        this.assertBounds();
        return result;
      }
      b = this.buf[this.pos++];
      result |= (b & 127) << 14;
      if ((b & 128) === 0) {
        this.assertBounds();
        return result;
      }
      b = this.buf[this.pos++];
      result |= (b & 127) << 21;
      if ((b & 128) === 0) {
        this.assertBounds();
        return result;
      }
      b = this.buf[this.pos++];
      result |= (b & 15) << 28;
      for (let readBytes = 5; (b & 128) !== 0 && readBytes < 10; readBytes++)
        b = this.buf[this.pos++];
      if ((b & 128) !== 0)
        throw new Error("invalid varint");
      this.assertBounds();
      return result >>> 0;
    }
  }
});

// hardware/generic/goldfish/emulator/ui/aquarium/node_modules/@bufbuild/protobuf/dist/commonjs/proto-int64.js
var require_proto_int64 = __commonJS({
  "hardware/generic/goldfish/emulator/ui/aquarium/node_modules/@bufbuild/protobuf/dist/commonjs/proto-int64.js"(exports2) {
    "use strict";
    Object.defineProperty(exports2, "__esModule", { value: true });
    exports2.protoInt64 = void 0;
    var varint_js_1 = require_varint();
    exports2.protoInt64 = makeInt64Support();
    function makeInt64Support() {
      const dv = new DataView(new ArrayBuffer(8));
      const ok = typeof BigInt === "function" && typeof dv.getBigInt64 === "function" && typeof dv.getBigUint64 === "function" && typeof dv.setBigInt64 === "function" && typeof dv.setBigUint64 === "function" && (!!globalThis.Deno || !!globalThis.Bun || typeof process != "object" || typeof process.env != "object" || process.env.BUF_BIGINT_DISABLE !== "1");
      if (ok) {
        const MIN = BigInt("-9223372036854775808");
        const MAX = BigInt("9223372036854775807");
        const UMIN = BigInt("0");
        const UMAX = BigInt("18446744073709551615");
        return {
          zero: BigInt(0),
          supported: true,
          parse(value) {
            const bi = typeof value == "bigint" ? value : BigInt(value);
            if (bi > MAX || bi < MIN) {
              throw new Error(`invalid int64: ${value}`);
            }
            return bi;
          },
          uParse(value) {
            const bi = typeof value == "bigint" ? value : BigInt(value);
            if (bi > UMAX || bi < UMIN) {
              throw new Error(`invalid uint64: ${value}`);
            }
            return bi;
          },
          enc(value) {
            dv.setBigInt64(0, this.parse(value), true);
            return {
              lo: dv.getInt32(0, true),
              hi: dv.getInt32(4, true)
            };
          },
          uEnc(value) {
            dv.setBigInt64(0, this.uParse(value), true);
            return {
              lo: dv.getInt32(0, true),
              hi: dv.getInt32(4, true)
            };
          },
          dec(lo, hi) {
            dv.setInt32(0, lo, true);
            dv.setInt32(4, hi, true);
            return dv.getBigInt64(0, true);
          },
          uDec(lo, hi) {
            dv.setInt32(0, lo, true);
            dv.setInt32(4, hi, true);
            return dv.getBigUint64(0, true);
          }
        };
      }
      return {
        zero: "0",
        supported: false,
        parse(value) {
          if (typeof value != "string") {
            value = value.toString();
          }
          assertInt64String(value);
          return value;
        },
        uParse(value) {
          if (typeof value != "string") {
            value = value.toString();
          }
          assertUInt64String(value);
          return value;
        },
        enc(value) {
          if (typeof value != "string") {
            value = value.toString();
          }
          assertInt64String(value);
          return (0, varint_js_1.int64FromString)(value);
        },
        uEnc(value) {
          if (typeof value != "string") {
            value = value.toString();
          }
          assertUInt64String(value);
          return (0, varint_js_1.int64FromString)(value);
        },
        dec(lo, hi) {
          return (0, varint_js_1.int64ToString)(lo, hi);
        },
        uDec(lo, hi) {
          return (0, varint_js_1.uInt64ToString)(lo, hi);
        }
      };
    }
    function assertInt64String(value) {
      if (!/^-?[0-9]+$/.test(value)) {
        throw new Error("invalid int64: " + value);
      }
    }
    function assertUInt64String(value) {
      if (!/^[0-9]+$/.test(value)) {
        throw new Error("invalid uint64: " + value);
      }
    }
  }
});

// hardware/generic/goldfish/emulator/ui/aquarium/node_modules/@bufbuild/protobuf/dist/commonjs/wire/text-encoding.js
var require_text_encoding = __commonJS({
  "hardware/generic/goldfish/emulator/ui/aquarium/node_modules/@bufbuild/protobuf/dist/commonjs/wire/text-encoding.js"(exports2) {
    "use strict";
    Object.defineProperty(exports2, "__esModule", { value: true });
    exports2.configureTextEncoding = configureTextEncoding;
    exports2.getTextEncoding = getTextEncoding;
    exports2.emulateEncodeInto = emulateEncodeInto;
    var symbol = Symbol.for("@bufbuild/protobuf/text-encoding");
    function configureTextEncoding(textEncoding) {
      var _a;
      globalThis[symbol] = Object.assign(Object.assign({}, textEncoding), { encodeUtf8Into: (_a = textEncoding.encodeUtf8Into) !== null && _a !== void 0 ? _a : emulateEncodeInto(textEncoding.encodeUtf8.bind(textEncoding)) });
    }
    function getTextEncoding() {
      const globals = globalThis;
      if (!globals[symbol]) {
        const textEncoder = new globals.TextEncoder();
        const textDecoder = new globals.TextDecoder();
        let textDecoderStrict;
        const config = {
          encodeUtf8(text) {
            return textEncoder.encode(text);
          },
          decodeUtf8(bytes, strict) {
            if (strict) {
              if (!textDecoderStrict) {
                textDecoderStrict = new globals.TextDecoder("utf-8", {
                  fatal: true
                });
              }
              return textDecoderStrict.decode(bytes);
            }
            return textDecoder.decode(bytes);
          },
          checkUtf8(text) {
            try {
              encodeURIComponent(text);
              return true;
            } catch (_) {
              return false;
            }
          }
        };
        if (textEncoder.encodeInto) {
          config.encodeUtf8Into = textEncoder.encodeInto.bind(textEncoder);
        }
        const nativeStringIsWellFormed = String.prototype.isWellFormed;
        if (nativeStringIsWellFormed) {
          config.checkUtf8 = (text) => {
            return nativeStringIsWellFormed.call(text);
          };
        }
        configureTextEncoding(config);
      }
      return globals[symbol];
    }
    function emulateEncodeInto(encodeUtf8) {
      return (text, dest) => {
        const bytes = encodeUtf8(text);
        dest.set(bytes);
        return { written: bytes.byteLength };
      };
    }
  }
});

// hardware/generic/goldfish/emulator/ui/aquarium/node_modules/@bufbuild/protobuf/dist/commonjs/wire/binary-encoding.js
var require_binary_encoding = __commonJS({
  "hardware/generic/goldfish/emulator/ui/aquarium/node_modules/@bufbuild/protobuf/dist/commonjs/wire/binary-encoding.js"(exports2) {
    "use strict";
    Object.defineProperty(exports2, "__esModule", { value: true });
    exports2.BinaryReader = exports2.BinaryWriter = exports2.INT32_MIN = exports2.INT32_MAX = exports2.UINT32_MAX = exports2.FLOAT32_MIN = exports2.FLOAT32_MAX = exports2.WireType = void 0;
    var varint_js_1 = require_varint();
    var proto_int64_js_1 = require_proto_int64();
    var text_encoding_js_1 = require_text_encoding();
    var WireType;
    (function(WireType2) {
      WireType2[WireType2["Varint"] = 0] = "Varint";
      WireType2[WireType2["Bit64"] = 1] = "Bit64";
      WireType2[WireType2["LengthDelimited"] = 2] = "LengthDelimited";
      WireType2[WireType2["StartGroup"] = 3] = "StartGroup";
      WireType2[WireType2["EndGroup"] = 4] = "EndGroup";
      WireType2[WireType2["Bit32"] = 5] = "Bit32";
    })(WireType || (exports2.WireType = WireType = {}));
    exports2.FLOAT32_MAX = 34028234663852886e22;
    exports2.FLOAT32_MIN = -34028234663852886e22;
    exports2.UINT32_MAX = 4294967295;
    exports2.INT32_MAX = 2147483647;
    exports2.INT32_MIN = -2147483648;
    var BinaryWriter = class {
      constructor(encodeUtf8) {
        this.stackPos = [];
        this.encodeUtf8Into = encodeUtf8 ? (0, text_encoding_js_1.emulateEncodeInto)(encodeUtf8) : (0, text_encoding_js_1.getTextEncoding)().encodeUtf8Into;
        this.buffer = EMPTY_BUFFER;
        this.viewCache = EMPTY_VIEW;
        this.pos = 0;
      }
      ensureCapacity(size) {
        const required = this.pos + size;
        if (required > this.buffer.length) {
          let newLen = this.buffer.length || INITIAL_SIZE;
          while (newLen < required)
            newLen *= 2;
          const newBuf = new Uint8Array(newLen);
          if (this.pos > 0)
            newBuf.set(this.buffer);
          this.buffer = newBuf;
        }
      }
      /**
       * The DataView over `buffer`, rebuilt only if the buffer has grown since it
       * was last used.
       */
      view() {
        const bytes = this.buffer;
        const view = this.viewCache;
        if (view.byteLength === bytes.byteLength)
          return view;
        const newView = new DataView(bytes.buffer);
        this.viewCache = newView;
        return newView;
      }
      /**
       * Return all bytes written and reset this writer.
       */
      finish() {
        const result = this.buffer.slice(0, this.pos);
        this.pos = 0;
        this.stackPos = [];
        return result;
      }
      /**
       * Start a new fork for length-delimited data like a message
       * or a packed repeated field.
       *
       * Must be joined later with `join()`.
       */
      fork() {
        this.stackPos.push(this.pos);
        this.ensureCapacity(DEFAULT_LEN_PREFIX_SIZE);
        this.buffer[this.pos++] = 0;
        return this;
      }
      /**
       * Join the last fork. Write its length and bytes, then
       * return to the previous state.
       */
      join() {
        const forkPos = this.stackPos.pop();
        if (forkPos === void 0)
          throw new Error("invalid state, fork stack empty");
        const len = this.pos - forkPos - DEFAULT_LEN_PREFIX_SIZE;
        const lenPrefixSize = varint32Size(len);
        if (lenPrefixSize > DEFAULT_LEN_PREFIX_SIZE) {
          this.ensureCapacity(lenPrefixSize - DEFAULT_LEN_PREFIX_SIZE);
          this.buffer.copyWithin(forkPos + lenPrefixSize, forkPos + DEFAULT_LEN_PREFIX_SIZE, this.pos);
        }
        this.pos = forkPos;
        this.uint32(len);
        this.pos += len;
        return this;
      }
      /**
       * Writes a tag (field number and wire type).
       *
       * Equivalent to `uint32( (fieldNo << 3 | type) >>> 0 )`.
       *
       * Generated code should compute the tag ahead of time and call `uint32()`.
       */
      tag(fieldNo, type) {
        return this.uint32((fieldNo << 3 | type) >>> 0);
      }
      /**
       * Write a chunk of raw bytes.
       */
      raw(chunk) {
        this.ensureCapacity(chunk.length);
        this.buffer.set(chunk, this.pos);
        this.pos += chunk.length;
        return this;
      }
      /**
       * Write a `uint32` value, an unsigned 32 bit varint.
       */
      uint32(value) {
        assertUInt32(value);
        this.ensureCapacity(5);
        if (value < 128) {
          this.buffer[this.pos++] = value;
          return this;
        }
        while (value > 127) {
          this.buffer[this.pos++] = value & 127 | 128;
          value >>>= 7;
        }
        this.buffer[this.pos++] = value;
        return this;
      }
      /**
       * Write a `int32` value, a signed 32 bit varint.
       */
      int32(value) {
        assertInt32(value);
        if (value >= 0) {
          return this.uint32(value);
        }
        this.ensureCapacity(10);
        for (let i = 0; i < 9; i++) {
          this.buffer[this.pos++] = value & 127 | 128;
          value >>= 7;
        }
        this.buffer[this.pos++] = 1;
        return this;
      }
      /**
       * Write a `bool` value, a varint.
       */
      bool(value) {
        this.ensureCapacity(1);
        this.buffer[this.pos++] = value ? 1 : 0;
        return this;
      }
      /**
       * Write a `bytes` value, length-delimited arbitrary data.
       */
      bytes(value) {
        this.uint32(value.byteLength);
        return this.raw(value);
      }
      /**
       * Write a `string` value, length-delimited data converted to UTF-8 text.
       */
      string(value) {
        if (typeof value !== "string") {
          value = String(value);
        }
        const len = value.length;
        if (len <= ASCII_MAX_LENGTH) {
          this.ensureCapacity(len + 1);
          const ascii = this.buffer;
          let pos = this.pos;
          ascii[pos++] = len;
          let i = 0;
          for (; i < len; i++) {
            const code = value.charCodeAt(i);
            if (code > 127)
              break;
            ascii[pos++] = code;
          }
          if (i == len) {
            this.pos = pos;
            return this;
          }
        }
        this.ensureCapacity(len * 3 + 5);
        const lenPrefixSizeGuess = varint32Size(len);
        const buf = this.buffer;
        const start = this.pos;
        const { written } = this.encodeUtf8Into(value, buf.subarray(start + lenPrefixSizeGuess));
        const lenPrefixSize = varint32Size(written);
        if (lenPrefixSize != lenPrefixSizeGuess) {
          buf.copyWithin(start + lenPrefixSize, start + lenPrefixSizeGuess, start + lenPrefixSizeGuess + written);
        }
        this.uint32(written);
        this.pos += written;
        return this;
      }
      /**
       * Write a `float` value, 32-bit floating point number.
       */
      float(value) {
        assertFloat32(value);
        this.ensureCapacity(4);
        this.view().setFloat32(this.pos, value, true);
        this.pos += 4;
        return this;
      }
      /**
       * Write a `double` value, a 64-bit floating point number.
       */
      double(value) {
        this.ensureCapacity(8);
        this.view().setFloat64(this.pos, value, true);
        this.pos += 8;
        return this;
      }
      /**
       * Write a `fixed32` value, an unsigned, fixed-length 32-bit integer.
       */
      fixed32(value) {
        assertUInt32(value);
        this.ensureCapacity(4);
        this.view().setUint32(this.pos, value, true);
        this.pos += 4;
        return this;
      }
      /**
       * Write a `sfixed32` value, a signed, fixed-length 32-bit integer.
       */
      sfixed32(value) {
        assertInt32(value);
        this.ensureCapacity(4);
        this.view().setInt32(this.pos, value, true);
        this.pos += 4;
        return this;
      }
      /**
       * Write a `sint32` value, a signed, zigzag-encoded 32-bit varint.
       */
      sint32(value) {
        assertInt32(value);
        return this.uint32((value << 1 ^ value >> 31) >>> 0);
      }
      /**
       * Write a `sfixed64` value, a signed, fixed-length 64-bit integer.
       */
      sfixed64(value) {
        const tc = proto_int64_js_1.protoInt64.enc(value);
        this.ensureCapacity(8);
        const view = this.view();
        view.setInt32(this.pos, tc.lo, true);
        view.setInt32(this.pos + 4, tc.hi, true);
        this.pos += 8;
        return this;
      }
      /**
       * Write a `fixed64` value, an unsigned, fixed-length 64 bit integer.
       */
      fixed64(value) {
        const tc = proto_int64_js_1.protoInt64.uEnc(value);
        this.ensureCapacity(8);
        const view = this.view();
        view.setInt32(this.pos, tc.lo, true);
        view.setInt32(this.pos + 4, tc.hi, true);
        this.pos += 8;
        return this;
      }
      /**
       * Write a `int64` value, a signed 64-bit varint.
       */
      int64(value) {
        const tc = proto_int64_js_1.protoInt64.enc(value);
        return this.writeVarint64(tc.lo, tc.hi);
      }
      /**
       * Write a `sint64` value, a signed, zig-zag-encoded 64-bit varint.
       */
      sint64(value) {
        const tc = proto_int64_js_1.protoInt64.enc(value), sign = tc.hi >> 31, lo = tc.lo << 1 ^ sign, hi = (tc.hi << 1 | tc.lo >>> 31) ^ sign;
        return this.writeVarint64(lo, hi);
      }
      /**
       * Write a `uint64` value, an unsigned 64-bit varint.
       */
      uint64(value) {
        const tc = proto_int64_js_1.protoInt64.uEnc(value);
        return this.writeVarint64(tc.lo, tc.hi);
      }
      /**
       * Write a 64-bit varint directly into the buffer. Accepts the value as
       * split low/high 32-bit words.
       *
       * Ported from varint64write() to avoid the intermediate number[] buffer.
       * See https://github.com/protocolbuffers/protobuf/blob/8a71927d74a4ce34efe2d8769fda198f52d20d12/js/experimental/runtime/kernel/writer.js#L344
       */
      writeVarint64(lo, hi) {
        this.ensureCapacity(10);
        const buf = this.buffer;
        let pos = this.pos;
        for (let i = 0; i < 28; i = i + 7) {
          const shift = lo >>> i;
          const hasNext = !(shift >>> 7 == 0 && hi == 0);
          buf[pos++] = (hasNext ? shift | 128 : shift) & 255;
          if (!hasNext) {
            this.pos = pos;
            return this;
          }
        }
        const splitBits = lo >>> 28 & 15 | (hi & 7) << 4;
        const hasMoreBits = !(hi >> 3 == 0);
        buf[pos++] = (hasMoreBits ? splitBits | 128 : splitBits) & 255;
        if (!hasMoreBits) {
          this.pos = pos;
          return this;
        }
        for (let i = 3; i < 31; i = i + 7) {
          const shift = hi >>> i;
          const hasNext = !(shift >>> 7 == 0);
          buf[pos++] = (hasNext ? shift | 128 : shift) & 255;
          if (!hasNext) {
            this.pos = pos;
            return this;
          }
        }
        buf[pos++] = hi >>> 31 & 1;
        this.pos = pos;
        return this;
      }
    };
    exports2.BinaryWriter = BinaryWriter;
    var INITIAL_SIZE = 128;
    var DEFAULT_LEN_PREFIX_SIZE = 1;
    var EMPTY_BUFFER = new Uint8Array(0);
    var EMPTY_VIEW = new DataView(EMPTY_BUFFER.buffer);
    var ASCII_MAX_LENGTH = 32;
    function varint32Size(value) {
      if (value < 128)
        return 1;
      if (value < 16384)
        return 2;
      if (value < 2097152)
        return 3;
      if (value < 268435456)
        return 4;
      return 5;
    }
    var BinaryReader = class {
      constructor(buf, decodeUtf8 = (0, text_encoding_js_1.getTextEncoding)().decodeUtf8) {
        this.decodeUtf8 = decodeUtf8;
        this.varint64Lo = 0;
        this.varint64Hi = 0;
        this.varint64 = varint_js_1.varint64read;
        this.uint32 = varint_js_1.varint32read;
        this.buf = buf;
        this.len = buf.length;
        this.pos = 0;
        this.view = new DataView(buf.buffer, buf.byteOffset, buf.byteLength);
      }
      /**
       * Reads a tag - field number and wire type. Tags are uint32 varints; values
       * that do not fit in uint32 are rejected.
       */
      tag() {
        const start = this.pos;
        const tag = this.uint32();
        const bytesRead = this.pos - start;
        if (bytesRead > 5 || bytesRead == 5 && this.buf[this.pos - 1] > 15) {
          throw new Error("illegal tag: varint overflows uint32");
        }
        const fieldNo = tag >>> 3;
        const wireType = tag & 7;
        if (fieldNo <= 0 || wireType > 5) {
          throw new Error("illegal tag: field no " + fieldNo + " wire type " + wireType);
        }
        return [fieldNo, wireType];
      }
      /**
       * Skip one element and return the skipped data.
       *
       * When skipping StartGroup, provide the tags field number to check for
       * matching field number in the EndGroup tag. Recursion into nested groups
       * is guarded by the `recursionLimit` argument: When the limit is reached,
       * this method throws.
       */
      skip(wireType, fieldNo, recursionLimit = 100) {
        let start = this.pos;
        switch (wireType) {
          case WireType.Varint:
            while (this.buf[this.pos++] & 128) {
            }
            break;
          case WireType.Bit64:
            this.pos += 4;
          case WireType.Bit32:
            this.pos += 4;
            break;
          case WireType.LengthDelimited:
            let len = this.uint32();
            this.pos += len;
            break;
          case WireType.StartGroup:
            if (recursionLimit <= 0) {
              throw new Error("maximum recursion depth reached");
            }
            for (; ; ) {
              const [fn, wt] = this.tag();
              if (wt === WireType.EndGroup) {
                if (fieldNo !== void 0 && fn !== fieldNo) {
                  throw new Error("invalid end group tag");
                }
                break;
              }
              this.skip(wt, fn, recursionLimit - 1);
            }
            break;
          default:
            throw new Error("cant skip wire type " + wireType);
        }
        this.assertBounds();
        return this.buf.subarray(start, this.pos);
      }
      /**
       * Throws error if position in byte array is out of range.
       */
      assertBounds() {
        if (this.pos > this.len)
          throw new RangeError("premature EOF");
      }
      /**
       * Read a `int32` field, a signed 32 bit varint.
       */
      int32() {
        return this.uint32() | 0;
      }
      /**
       * Read a `sint32` field, a signed, zigzag-encoded 32-bit varint.
       */
      sint32() {
        let zze = this.uint32();
        return zze >>> 1 ^ -(zze & 1);
      }
      /**
       * Read a `int64` field, a signed 64-bit varint.
       */
      int64() {
        this.varint64();
        return proto_int64_js_1.protoInt64.dec(this.varint64Lo, this.varint64Hi);
      }
      /**
       * Read a `uint64` field, an unsigned 64-bit varint.
       */
      uint64() {
        this.varint64();
        return proto_int64_js_1.protoInt64.uDec(this.varint64Lo, this.varint64Hi);
      }
      /**
       * Read a `sint64` field, a signed, zig-zag-encoded 64-bit varint.
       */
      sint64() {
        this.varint64();
        let lo = this.varint64Lo;
        let hi = this.varint64Hi;
        let s = -(lo & 1);
        lo = (lo >>> 1 | (hi & 1) << 31) ^ s;
        hi = hi >>> 1 ^ s;
        return proto_int64_js_1.protoInt64.dec(lo, hi);
      }
      /**
       * Read a `bool` field, a variant.
       */
      bool() {
        const b = this.buf[this.pos];
        if (b < 128) {
          this.pos++;
          return b !== 0;
        }
        this.varint64();
        return this.varint64Lo !== 0 || this.varint64Hi !== 0;
      }
      /**
       * Read a `fixed32` field, an unsigned, fixed-length 32-bit integer.
       */
      fixed32() {
        return this.view.getUint32((this.pos += 4) - 4, true);
      }
      /**
       * Read a `sfixed32` field, a signed, fixed-length 32-bit integer.
       */
      sfixed32() {
        return this.view.getInt32((this.pos += 4) - 4, true);
      }
      /**
       * Read a `fixed64` field, an unsigned, fixed-length 64 bit integer.
       */
      fixed64() {
        return proto_int64_js_1.protoInt64.uDec(this.sfixed32(), this.sfixed32());
      }
      /**
       * Read a `fixed64` field, a signed, fixed-length 64-bit integer.
       */
      sfixed64() {
        return proto_int64_js_1.protoInt64.dec(this.sfixed32(), this.sfixed32());
      }
      /**
       * Read a `float` field, 32-bit floating point number.
       */
      float() {
        return this.view.getFloat32((this.pos += 4) - 4, true);
      }
      /**
       * Read a `double` field, a 64-bit floating point number.
       */
      double() {
        return this.view.getFloat64((this.pos += 8) - 8, true);
      }
      /**
       * Read a `bytes` field, length-delimited arbitrary data.
       */
      bytes() {
        let len = this.uint32(), start = this.pos;
        this.pos += len;
        this.assertBounds();
        return this.buf.subarray(start, start + len);
      }
      /**
       * Read a `string` field, length-delimited data converted to UTF-8 text. If
       * `strict` is true, throw on invalid UTF-8 instead of substituting U+FFFD.
       */
      string(strict) {
        const bytes = this.bytes();
        const len = bytes.length;
        if (len <= ASCII_MAX_LENGTH) {
          const codes = new Array(len);
          for (let i = 0; i < len; i++) {
            const byte = bytes[i];
            if (byte > 127) {
              return this.decodeUtf8(bytes, strict);
            }
            codes[i] = byte;
          }
          return String.fromCharCode.apply(String, codes);
        }
        return this.decodeUtf8(bytes, strict);
      }
    };
    exports2.BinaryReader = BinaryReader;
    function assertInt32(arg) {
      if (typeof arg == "string") {
        arg = Number(arg);
      } else if (typeof arg != "number") {
        throw new Error("invalid int32: " + typeof arg);
      }
      if (!Number.isInteger(arg) || arg > exports2.INT32_MAX || arg < exports2.INT32_MIN)
        throw new Error("invalid int32: " + arg);
    }
    function assertUInt32(arg) {
      if (typeof arg == "string") {
        arg = Number(arg);
      } else if (typeof arg != "number") {
        throw new Error("invalid uint32: " + typeof arg);
      }
      if (!Number.isInteger(arg) || arg > exports2.UINT32_MAX || arg < 0)
        throw new Error("invalid uint32: " + arg);
    }
    function assertFloat32(arg) {
      if (typeof arg == "string") {
        const o = arg;
        arg = Number(arg);
        if (Number.isNaN(arg) && o !== "NaN") {
          throw new Error("invalid float32: " + o);
        }
      } else if (typeof arg != "number") {
        throw new Error("invalid float32: " + typeof arg);
      }
      if (Number.isFinite(arg) && (arg > exports2.FLOAT32_MAX || arg < exports2.FLOAT32_MIN))
        throw new Error("invalid float32: " + arg);
    }
  }
});

// hardware/generic/goldfish/emulator/ui/aquarium/node_modules/@bufbuild/protobuf/dist/commonjs/wire/base64-encoding.js
var require_base64_encoding = __commonJS({
  "hardware/generic/goldfish/emulator/ui/aquarium/node_modules/@bufbuild/protobuf/dist/commonjs/wire/base64-encoding.js"(exports2) {
    "use strict";
    Object.defineProperty(exports2, "__esModule", { value: true });
    exports2.base64Decode = base64Decode;
    exports2.base64Encode = base64Encode;
    var nativeSetFromBase64 = Uint8Array.prototype.setFromBase64;
    function base64Decode(base64Str) {
      const len = base64Str.length;
      let size = len - (len + 3 >> 2);
      if ((len & 3) == 0 && base64Str[len - 1] == "=") {
        size -= base64Str[len - 2] == "=" ? 2 : 1;
      }
      const bytes = new Uint8Array(size);
      let written = -1;
      if (nativeSetFromBase64) {
        try {
          const result = nativeSetFromBase64.call(bytes, base64Str);
          if (result.read == len) {
            written = result.written;
          }
        } catch (_a) {
        }
      }
      if (written < 0) {
        written = setFromBase64(bytes, base64Str);
      }
      return written == size ? bytes : bytes.subarray(0, written);
    }
    function setFromBase64(bytes, base64Str) {
      const table = getDecodeTable();
      let bytePos = 0, groupPos = 0, b, p = 0;
      for (let i = 0; i < base64Str.length; i++) {
        b = table[base64Str.charCodeAt(i)];
        if (b === void 0) {
          switch (base64Str[i]) {
            case "=":
              groupPos = 0;
            case "\n":
            case "\r":
            case "	":
            case " ":
              continue;
            default:
              throw Error("invalid base64 string");
          }
        }
        switch (groupPos) {
          case 0:
            p = b;
            groupPos = 1;
            break;
          case 1:
            bytes[bytePos++] = p << 2 | (b & 48) >> 4;
            p = b;
            groupPos = 2;
            break;
          case 2:
            bytes[bytePos++] = (p & 15) << 4 | (b & 60) >> 2;
            p = b;
            groupPos = 3;
            break;
          case 3:
            bytes[bytePos++] = (p & 3) << 6 | b;
            groupPos = 0;
            break;
        }
      }
      if (groupPos == 1)
        throw Error("invalid base64 string");
      return bytePos;
    }
    var nativeToBase64 = Uint8Array.prototype.toBase64;
    var toBase64OptionsMap = {
      std: { alphabet: "base64", omitPadding: false },
      std_raw: { alphabet: "base64", omitPadding: true },
      url: { alphabet: "base64url", omitPadding: true }
    };
    function base64Encode(bytes, encoding = "std") {
      if (nativeToBase64) {
        return nativeToBase64.call(bytes, toBase64OptionsMap[encoding]);
      }
      const table = getEncodeTable(encoding);
      const pad = encoding == "std";
      let base64 = "", groupPos = 0, b, p = 0;
      for (let i = 0; i < bytes.length; i++) {
        b = bytes[i];
        switch (groupPos) {
          case 0:
            base64 += table[b >> 2];
            p = (b & 3) << 4;
            groupPos = 1;
            break;
          case 1:
            base64 += table[p | b >> 4];
            p = (b & 15) << 2;
            groupPos = 2;
            break;
          case 2:
            base64 += table[p | b >> 6];
            base64 += table[b & 63];
            groupPos = 0;
            break;
        }
      }
      if (groupPos) {
        base64 += table[p];
        if (pad) {
          base64 += "=";
          if (groupPos == 1)
            base64 += "=";
        }
      }
      return base64;
    }
    var encodeTableStd;
    var encodeTableUrl;
    var decodeTable;
    function getEncodeTable(encoding) {
      if (!encodeTableStd) {
        encodeTableStd = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/".split("");
        encodeTableUrl = encodeTableStd.slice(0, -2).concat("-", "_");
      }
      return encoding == "url" ? (
        // biome-ignore lint/style/noNonNullAssertion: TS fails to narrow down
        encodeTableUrl
      ) : encodeTableStd;
    }
    function getDecodeTable() {
      if (!decodeTable) {
        decodeTable = [];
        const encodeTable = getEncodeTable("std");
        for (let i = 0; i < encodeTable.length; i++)
          decodeTable[encodeTable[i].charCodeAt(0)] = i;
        decodeTable["-".charCodeAt(0)] = encodeTable.indexOf("+");
        decodeTable["_".charCodeAt(0)] = encodeTable.indexOf("/");
      }
      return decodeTable;
    }
  }
});

// hardware/generic/goldfish/emulator/ui/aquarium/node_modules/@bufbuild/protobuf/dist/commonjs/descriptors.js
var require_descriptors = __commonJS({
  "hardware/generic/goldfish/emulator/ui/aquarium/node_modules/@bufbuild/protobuf/dist/commonjs/descriptors.js"(exports2) {
    "use strict";
    Object.defineProperty(exports2, "__esModule", { value: true });
    exports2.ScalarType = void 0;
    var ScalarType;
    (function(ScalarType2) {
      ScalarType2[ScalarType2["DOUBLE"] = 1] = "DOUBLE";
      ScalarType2[ScalarType2["FLOAT"] = 2] = "FLOAT";
      ScalarType2[ScalarType2["INT64"] = 3] = "INT64";
      ScalarType2[ScalarType2["UINT64"] = 4] = "UINT64";
      ScalarType2[ScalarType2["INT32"] = 5] = "INT32";
      ScalarType2[ScalarType2["FIXED64"] = 6] = "FIXED64";
      ScalarType2[ScalarType2["FIXED32"] = 7] = "FIXED32";
      ScalarType2[ScalarType2["BOOL"] = 8] = "BOOL";
      ScalarType2[ScalarType2["STRING"] = 9] = "STRING";
      ScalarType2[ScalarType2["BYTES"] = 12] = "BYTES";
      ScalarType2[ScalarType2["UINT32"] = 13] = "UINT32";
      ScalarType2[ScalarType2["SFIXED32"] = 15] = "SFIXED32";
      ScalarType2[ScalarType2["SFIXED64"] = 16] = "SFIXED64";
      ScalarType2[ScalarType2["SINT32"] = 17] = "SINT32";
      ScalarType2[ScalarType2["SINT64"] = 18] = "SINT64";
    })(ScalarType || (exports2.ScalarType = ScalarType = {}));
  }
});

// hardware/generic/goldfish/emulator/ui/aquarium/node_modules/@bufbuild/protobuf/dist/commonjs/wire/text-format.js
var require_text_format = __commonJS({
  "hardware/generic/goldfish/emulator/ui/aquarium/node_modules/@bufbuild/protobuf/dist/commonjs/wire/text-format.js"(exports2) {
    "use strict";
    Object.defineProperty(exports2, "__esModule", { value: true });
    exports2.parseTextFormatEnumValue = parseTextFormatEnumValue;
    exports2.parseTextFormatScalarValue = parseTextFormatScalarValue;
    var descriptors_js_1 = require_descriptors();
    var proto_int64_js_1 = require_proto_int64();
    function parseTextFormatEnumValue(descEnum, value) {
      const enumValue = descEnum.values.find((v) => v.name === value);
      if (!enumValue) {
        throw new Error(`cannot parse ${descEnum} default value: ${value}`);
      }
      return enumValue.number;
    }
    function parseTextFormatScalarValue(type, value) {
      switch (type) {
        case descriptors_js_1.ScalarType.STRING:
          return value;
        case descriptors_js_1.ScalarType.BYTES: {
          const u = unescapeBytesDefaultValue(value);
          if (u === false) {
            throw new Error(`cannot parse ${descriptors_js_1.ScalarType[type]} default value: ${value}`);
          }
          return u;
        }
        case descriptors_js_1.ScalarType.INT64:
        case descriptors_js_1.ScalarType.SFIXED64:
        case descriptors_js_1.ScalarType.SINT64:
          return proto_int64_js_1.protoInt64.parse(value);
        case descriptors_js_1.ScalarType.UINT64:
        case descriptors_js_1.ScalarType.FIXED64:
          return proto_int64_js_1.protoInt64.uParse(value);
        case descriptors_js_1.ScalarType.DOUBLE:
        case descriptors_js_1.ScalarType.FLOAT:
          switch (value) {
            case "inf":
              return Number.POSITIVE_INFINITY;
            case "-inf":
              return Number.NEGATIVE_INFINITY;
            case "nan":
              return Number.NaN;
            default:
              return parseFloat(value);
          }
        case descriptors_js_1.ScalarType.BOOL:
          return value === "true";
        case descriptors_js_1.ScalarType.INT32:
        case descriptors_js_1.ScalarType.UINT32:
        case descriptors_js_1.ScalarType.SINT32:
        case descriptors_js_1.ScalarType.FIXED32:
        case descriptors_js_1.ScalarType.SFIXED32:
          return parseInt(value, 10);
      }
    }
    function unescapeBytesDefaultValue(str) {
      const b = [];
      const input = {
        tail: str,
        c: "",
        next() {
          if (this.tail.length == 0) {
            return false;
          }
          this.c = this.tail[0];
          this.tail = this.tail.substring(1);
          return true;
        },
        take(n) {
          if (this.tail.length >= n) {
            const r = this.tail.substring(0, n);
            this.tail = this.tail.substring(n);
            return r;
          }
          return false;
        }
      };
      while (input.next()) {
        switch (input.c) {
          case "\\":
            if (input.next()) {
              switch (input.c) {
                case "\\":
                  b.push(input.c.charCodeAt(0));
                  break;
                case "b":
                  b.push(8);
                  break;
                case "f":
                  b.push(12);
                  break;
                case "n":
                  b.push(10);
                  break;
                case "r":
                  b.push(13);
                  break;
                case "t":
                  b.push(9);
                  break;
                case "v":
                  b.push(11);
                  break;
                case "0":
                case "1":
                case "2":
                case "3":
                case "4":
                case "5":
                case "6":
                case "7": {
                  const s = input.c;
                  const t = input.take(2);
                  if (t === false) {
                    return false;
                  }
                  const n = parseInt(s + t, 8);
                  if (Number.isNaN(n)) {
                    return false;
                  }
                  b.push(n);
                  break;
                }
                case "x": {
                  const s = input.c;
                  const t = input.take(2);
                  if (t === false) {
                    return false;
                  }
                  const n = parseInt(s + t, 16);
                  if (Number.isNaN(n)) {
                    return false;
                  }
                  b.push(n);
                  break;
                }
                case "u": {
                  const s = input.c;
                  const t = input.take(4);
                  if (t === false) {
                    return false;
                  }
                  const n = parseInt(s + t, 16);
                  if (Number.isNaN(n)) {
                    return false;
                  }
                  const chunk = new Uint8Array(4);
                  const view = new DataView(chunk.buffer);
                  view.setInt32(0, n, true);
                  b.push(chunk[0], chunk[1], chunk[2], chunk[3]);
                  break;
                }
                case "U": {
                  const s = input.c;
                  const t = input.take(8);
                  if (t === false) {
                    return false;
                  }
                  const tc = proto_int64_js_1.protoInt64.uEnc(s + t);
                  const chunk = new Uint8Array(8);
                  const view = new DataView(chunk.buffer);
                  view.setInt32(0, tc.lo, true);
                  view.setInt32(4, tc.hi, true);
                  b.push(chunk[0], chunk[1], chunk[2], chunk[3], chunk[4], chunk[5], chunk[6], chunk[7]);
                  break;
                }
              }
            }
            break;
          default:
            b.push(input.c.charCodeAt(0));
        }
      }
      return new Uint8Array(b);
    }
  }
});

// hardware/generic/goldfish/emulator/ui/aquarium/node_modules/@bufbuild/protobuf/dist/commonjs/reflect/error.js
var require_error = __commonJS({
  "hardware/generic/goldfish/emulator/ui/aquarium/node_modules/@bufbuild/protobuf/dist/commonjs/reflect/error.js"(exports2) {
    "use strict";
    Object.defineProperty(exports2, "__esModule", { value: true });
    exports2.FieldError = void 0;
    exports2.isFieldError = isFieldError;
    var errorNames = [
      "FieldValueInvalidError",
      "FieldListRangeError",
      "ForeignFieldError"
    ];
    var FieldError = class extends Error {
      constructor(fieldOrOneof, message, name = "FieldValueInvalidError") {
        super(message);
        this.name = name;
        this.field = () => fieldOrOneof;
      }
    };
    exports2.FieldError = FieldError;
    function isFieldError(arg) {
      return arg instanceof Error && errorNames.includes(arg.name) && "field" in arg && typeof arg.field == "function";
    }
  }
});

// hardware/generic/goldfish/emulator/ui/aquarium/node_modules/@bufbuild/protobuf/dist/commonjs/reflect/scalar.js
var require_scalar = __commonJS({
  "hardware/generic/goldfish/emulator/ui/aquarium/node_modules/@bufbuild/protobuf/dist/commonjs/reflect/scalar.js"(exports2) {
    "use strict";
    Object.defineProperty(exports2, "__esModule", { value: true });
    exports2.scalarEquals = scalarEquals;
    exports2.scalarZeroValue = scalarZeroValue;
    exports2.isScalarZeroValue = isScalarZeroValue;
    var proto_int64_js_1 = require_proto_int64();
    var descriptors_js_1 = require_descriptors();
    function scalarEquals(type, a, b) {
      if (a === b) {
        return true;
      }
      if (type == descriptors_js_1.ScalarType.BYTES) {
        if (!(a instanceof Uint8Array) || !(b instanceof Uint8Array)) {
          return false;
        }
        if (a.length !== b.length) {
          return false;
        }
        for (let i = 0; i < a.length; i++) {
          if (a[i] !== b[i]) {
            return false;
          }
        }
        return true;
      }
      switch (type) {
        case descriptors_js_1.ScalarType.UINT64:
        case descriptors_js_1.ScalarType.FIXED64:
        case descriptors_js_1.ScalarType.INT64:
        case descriptors_js_1.ScalarType.SFIXED64:
        case descriptors_js_1.ScalarType.SINT64:
          return a == b;
      }
      return false;
    }
    function scalarZeroValue(type, longAsString) {
      switch (type) {
        case descriptors_js_1.ScalarType.STRING:
          return "";
        case descriptors_js_1.ScalarType.BOOL:
          return false;
        case descriptors_js_1.ScalarType.DOUBLE:
        case descriptors_js_1.ScalarType.FLOAT:
          return 0;
        case descriptors_js_1.ScalarType.INT64:
        case descriptors_js_1.ScalarType.UINT64:
        case descriptors_js_1.ScalarType.SFIXED64:
        case descriptors_js_1.ScalarType.FIXED64:
        case descriptors_js_1.ScalarType.SINT64:
          return longAsString ? "0" : proto_int64_js_1.protoInt64.zero;
        case descriptors_js_1.ScalarType.BYTES:
          return new Uint8Array(0);
        default:
          return 0;
      }
    }
    function isScalarZeroValue(type, value) {
      switch (type) {
        case descriptors_js_1.ScalarType.BOOL:
          return value === false;
        case descriptors_js_1.ScalarType.STRING:
          return value === "";
        case descriptors_js_1.ScalarType.BYTES:
          return value instanceof Uint8Array && !value.byteLength;
        case descriptors_js_1.ScalarType.DOUBLE:
        case descriptors_js_1.ScalarType.FLOAT:
          return Object.is(value, 0);
        default:
          return value == 0;
      }
    }
  }
});

// hardware/generic/goldfish/emulator/ui/aquarium/node_modules/@bufbuild/protobuf/dist/commonjs/reflect/unsafe.js
var require_unsafe = __commonJS({
  "hardware/generic/goldfish/emulator/ui/aquarium/node_modules/@bufbuild/protobuf/dist/commonjs/reflect/unsafe.js"(exports2) {
    "use strict";
    Object.defineProperty(exports2, "__esModule", { value: true });
    exports2.unsafeLocal = void 0;
    exports2.unsafeOneofCase = unsafeOneofCase;
    exports2.unsafeIsSet = unsafeIsSet;
    exports2.unsafeIsSetExplicit = unsafeIsSetExplicit;
    exports2.unsafeGet = unsafeGet;
    exports2.unsafeSet = unsafeSet;
    exports2.unsafeClear = unsafeClear;
    var scalar_js_1 = require_scalar();
    var IMPLICIT = 2;
    exports2.unsafeLocal = Symbol.for("reflect unsafe local");
    function unsafeOneofCase(target, oneof) {
      const c = target[oneof.localName].case;
      if (c === void 0) {
        return c;
      }
      return oneof.fields.find((f) => f.localName === c);
    }
    function unsafeIsSet(target, field) {
      const name = field.localName;
      if (field.oneof) {
        return target[field.oneof.localName].case === name;
      }
      if (field.presence != IMPLICIT) {
        return target[name] !== void 0 && Object.prototype.hasOwnProperty.call(target, name);
      }
      switch (field.fieldKind) {
        case "list":
          return target[name].length > 0;
        case "map":
          return Object.keys(target[name]).length > 0;
        case "scalar":
          return !(0, scalar_js_1.isScalarZeroValue)(field.scalar, target[name]);
        case "enum":
          return target[name] !== field.enum.values[0].number;
      }
      throw new Error("message field with implicit presence");
    }
    function unsafeIsSetExplicit(target, localName) {
      return Object.prototype.hasOwnProperty.call(target, localName) && target[localName] !== void 0;
    }
    function unsafeGet(target, field) {
      if (field.oneof) {
        const oneof = target[field.oneof.localName];
        if (oneof.case === field.localName) {
          return oneof.value;
        }
        return void 0;
      }
      return target[field.localName];
    }
    function unsafeSet(target, field, value) {
      if (field.oneof) {
        target[field.oneof.localName] = {
          case: field.localName,
          value
        };
      } else {
        target[field.localName] = value;
      }
    }
    function unsafeClear(target, field) {
      const name = field.localName;
      if (field.oneof) {
        const oneofLocalName = field.oneof.localName;
        if (target[oneofLocalName].case === name) {
          target[oneofLocalName] = { case: void 0 };
        }
      } else if (field.presence != IMPLICIT) {
        delete target[name];
      } else {
        switch (field.fieldKind) {
          case "map":
            target[name] = {};
            break;
          case "list":
            target[name] = [];
            break;
          case "enum":
            target[name] = field.enum.values[0].number;
            break;
          case "scalar":
            target[name] = (0, scalar_js_1.scalarZeroValue)(field.scalar, field.longAsString);
            break;
        }
      }
    }
  }
});

// hardware/generic/goldfish/emulator/ui/aquarium/node_modules/@bufbuild/protobuf/dist/commonjs/is-message.js
var require_is_message = __commonJS({
  "hardware/generic/goldfish/emulator/ui/aquarium/node_modules/@bufbuild/protobuf/dist/commonjs/is-message.js"(exports2) {
    "use strict";
    Object.defineProperty(exports2, "__esModule", { value: true });
    exports2.isMessage = isMessage;
    function isMessage(arg, schema) {
      const isMessage2 = arg !== null && typeof arg == "object" && "$typeName" in arg && typeof arg.$typeName == "string";
      if (!isMessage2) {
        return false;
      }
      if (schema === void 0) {
        return true;
      }
      return schema.typeName === arg.$typeName;
    }
  }
});

// hardware/generic/goldfish/emulator/ui/aquarium/node_modules/@bufbuild/protobuf/dist/commonjs/reflect/guard.js
var require_guard = __commonJS({
  "hardware/generic/goldfish/emulator/ui/aquarium/node_modules/@bufbuild/protobuf/dist/commonjs/reflect/guard.js"(exports2) {
    "use strict";
    Object.defineProperty(exports2, "__esModule", { value: true });
    exports2.isObject = isObject;
    exports2.isOneofADT = isOneofADT;
    exports2.isReflectList = isReflectList;
    exports2.isReflectMap = isReflectMap;
    exports2.isReflectMessage = isReflectMessage;
    var unsafe_js_1 = require_unsafe();
    function isObject(arg) {
      return arg !== null && typeof arg == "object" && !Array.isArray(arg);
    }
    function isOneofADT(arg) {
      return arg !== null && typeof arg == "object" && "case" in arg && (typeof arg.case == "string" && "value" in arg && arg.value != null || arg.case === void 0 && (!("value" in arg) || arg.value === void 0));
    }
    function isReflectList(arg, field) {
      var _a, _b, _c, _d;
      if (isObject(arg) && unsafe_js_1.unsafeLocal in arg && "add" in arg && "field" in arg && typeof arg.field == "function") {
        if (field !== void 0) {
          const a = field;
          const b = arg.field();
          return a.listKind == b.listKind && a.scalar === b.scalar && ((_a = a.message) === null || _a === void 0 ? void 0 : _a.typeName) === ((_b = b.message) === null || _b === void 0 ? void 0 : _b.typeName) && ((_c = a.enum) === null || _c === void 0 ? void 0 : _c.typeName) === ((_d = b.enum) === null || _d === void 0 ? void 0 : _d.typeName);
        }
        return true;
      }
      return false;
    }
    function isReflectMap(arg, field) {
      var _a, _b, _c, _d;
      if (isObject(arg) && unsafe_js_1.unsafeLocal in arg && "has" in arg && "field" in arg && typeof arg.field == "function") {
        if (field !== void 0) {
          const a = field, b = arg.field();
          return a.mapKey === b.mapKey && a.mapKind == b.mapKind && a.scalar === b.scalar && ((_a = a.message) === null || _a === void 0 ? void 0 : _a.typeName) === ((_b = b.message) === null || _b === void 0 ? void 0 : _b.typeName) && ((_c = a.enum) === null || _c === void 0 ? void 0 : _c.typeName) === ((_d = b.enum) === null || _d === void 0 ? void 0 : _d.typeName);
        }
        return true;
      }
      return false;
    }
    function isReflectMessage(arg, messageDesc) {
      return isObject(arg) && unsafe_js_1.unsafeLocal in arg && "desc" in arg && isObject(arg.desc) && arg.desc.kind === "message" && (messageDesc === void 0 || arg.desc.typeName == messageDesc.typeName);
    }
  }
});

// hardware/generic/goldfish/emulator/ui/aquarium/node_modules/@bufbuild/protobuf/dist/commonjs/wkt/wrappers.js
var require_wrappers = __commonJS({
  "hardware/generic/goldfish/emulator/ui/aquarium/node_modules/@bufbuild/protobuf/dist/commonjs/wkt/wrappers.js"(exports2) {
    "use strict";
    Object.defineProperty(exports2, "__esModule", { value: true });
    exports2.isWrapper = isWrapper;
    exports2.isWrapperDesc = isWrapperDesc;
    exports2.hasCustomJsonRepresentation = hasCustomJsonRepresentation;
    function isWrapper(arg) {
      return isWrapperTypeName(arg.$typeName);
    }
    function isWrapperDesc(messageDesc) {
      const f = messageDesc.fields[0];
      return isWrapperTypeName(messageDesc.typeName) && f !== void 0 && f.fieldKind == "scalar" && f.name == "value" && f.number == 1;
    }
    function hasCustomJsonRepresentation(desc) {
      switch (desc.typeName) {
        case "google.protobuf.Any":
        case "google.protobuf.Timestamp":
        case "google.protobuf.Duration":
        case "google.protobuf.FieldMask":
        case "google.protobuf.Struct":
        case "google.protobuf.Value":
        case "google.protobuf.ListValue":
          return true;
        default:
          return isWrapperDesc(desc);
      }
    }
    var wrapperTypeNames = /* @__PURE__ */ new Set([
      "google.protobuf.DoubleValue",
      "google.protobuf.FloatValue",
      "google.protobuf.Int64Value",
      "google.protobuf.UInt64Value",
      "google.protobuf.Int32Value",
      "google.protobuf.UInt32Value",
      "google.protobuf.BoolValue",
      "google.protobuf.StringValue",
      "google.protobuf.BytesValue"
    ]);
    function isWrapperTypeName(name) {
      return wrapperTypeNames.has(name);
    }
  }
});

// hardware/generic/goldfish/emulator/ui/aquarium/node_modules/@bufbuild/protobuf/dist/commonjs/create.js
var require_create = __commonJS({
  "hardware/generic/goldfish/emulator/ui/aquarium/node_modules/@bufbuild/protobuf/dist/commonjs/create.js"(exports2) {
    "use strict";
    Object.defineProperty(exports2, "__esModule", { value: true });
    exports2.create = create;
    var is_message_js_1 = require_is_message();
    var descriptors_js_1 = require_descriptors();
    var scalar_js_1 = require_scalar();
    var guard_js_1 = require_guard();
    var wrappers_js_1 = require_wrappers();
    var EDITION_PROTO3 = 999;
    var EDITION_PROTO2 = 998;
    var IMPLICIT = 2;
    function create(schema, init) {
      if ((0, is_message_js_1.isMessage)(init, schema)) {
        return init;
      }
      return compiledCreate(schema)(init);
    }
    var compiledCreates = /* @__PURE__ */ new WeakMap();
    function compiledCreate(desc) {
      let compiled = compiledCreates.get(desc);
      if (compiled === void 0) {
        compiled = compileCreate(desc);
        compiledCreates.set(desc, compiled);
      }
      return compiled;
    }
    var INIT_SINGULAR = 0;
    var INIT_LIST = 1;
    var INIT_MAP = 2;
    var INIT_ONEOF = 3;
    function compileCreate(desc) {
      const typeName = desc.typeName;
      const { properties, prototype } = compileInitMessage(desc);
      return (init) => {
        let message;
        if (prototype !== void 0) {
          message = Object.create(prototype);
          message.$typeName = typeName;
        } else {
          message = { $typeName: typeName };
        }
        for (let i = 0; i < properties.length; i++) {
          const property = properties[i];
          const name = property.name;
          const initValue = init === null || init === void 0 ? void 0 : init[name];
          switch (property.kind) {
            case INIT_SINGULAR:
              if (initValue != null) {
                message[name] = property.convert !== void 0 ? property.convert(initValue) : initValue;
              } else if (property.constant !== void 0) {
                message[name] = property.constant;
              }
              break;
            case INIT_LIST:
              message[name] = property.convert !== void 0 && Array.isArray(initValue) ? initValue.map(property.convert) : initValue !== null && initValue !== void 0 ? initValue : [];
              break;
            case INIT_MAP:
              if (property.convert === void 0 || !(0, guard_js_1.isObject)(initValue)) {
                message[name] = initValue !== null && initValue !== void 0 ? initValue : {};
              } else {
                const converted = {};
                const keys = Object.keys(initValue);
                for (let k = 0; k < keys.length; k++) {
                  converted[keys[k]] = property.convert(initValue[keys[k]]);
                }
                message[name] = converted;
              }
              break;
            case INIT_ONEOF: {
              const oneofValue = initValue;
              if ((oneofValue === null || oneofValue === void 0 ? void 0 : oneofValue.case) != null) {
                const convert = property.convert.get(oneofValue.case);
                if (convert !== void 0) {
                  message[name] = {
                    case: oneofValue.case,
                    value: convert(oneofValue.value)
                  };
                  break;
                }
              }
              message[name] = { case: void 0 };
              break;
            }
          }
        }
        return message;
      };
    }
    function compileInitMessage(desc) {
      var _a, _b;
      const properties = [];
      const prototype = {};
      const usePrototype = needsPrototypeChain(desc);
      for (const member of desc.members) {
        const name = member.localName;
        if (member.kind == "oneof") {
          properties.push({
            name,
            kind: INIT_ONEOF,
            constant: void 0,
            convert: compileConvertOneof(member)
          });
          continue;
        }
        switch (member.fieldKind) {
          case "message": {
            properties.push({
              name,
              kind: INIT_SINGULAR,
              constant: void 0,
              convert: compileConvertMessage(member)
            });
            break;
          }
          case "list": {
            properties.push({
              name,
              kind: INIT_LIST,
              constant: void 0,
              convert: member.listKind == "message" ? (_a = compileConvertMessage(member)) !== null && _a !== void 0 ? _a : (value) => value : member.scalar == descriptors_js_1.ScalarType.BYTES ? toU8Arr : void 0
            });
            break;
          }
          case "map": {
            properties.push({
              name,
              kind: INIT_MAP,
              constant: void 0,
              convert: member.mapKind == "message" ? (_b = compileConvertMessage(member)) !== null && _b !== void 0 ? _b : (value) => value : member.scalar == descriptors_js_1.ScalarType.BYTES ? toU8Arr : void 0
            });
            break;
          }
          default: {
            const zeroValue = createZeroValue(member);
            properties.push({
              name,
              kind: INIT_SINGULAR,
              constant: member.presence == IMPLICIT ? zeroValue : void 0,
              convert: member.fieldKind == "scalar" && member.scalar == descriptors_js_1.ScalarType.BYTES ? toU8Arr : void 0
            });
            if (usePrototype) {
              prototype[name] = zeroValue;
            }
            break;
          }
        }
      }
      return {
        properties,
        prototype: usePrototype ? prototype : void 0
      };
    }
    function compileConvertOneof(oneof) {
      const converters = /* @__PURE__ */ new Map();
      for (const field of oneof.fields) {
        let convert;
        if (field.fieldKind == "message") {
          convert = compileConvertMessage(field);
        } else if (field.fieldKind == "scalar" && field.scalar == descriptors_js_1.ScalarType.BYTES) {
          convert = toU8Arr;
        }
        converters.set(field.localName, convert !== null && convert !== void 0 ? convert : (value) => value);
      }
      return converters;
    }
    function compileConvertMessage(field) {
      if (field.fieldKind == "message" && !field.oneof && (0, wrappers_js_1.isWrapperDesc)(field.message)) {
        return field.message.fields[0].scalar == descriptors_js_1.ScalarType.BYTES ? toU8Arr : void 0;
      }
      if (field.message.typeName == "google.protobuf.Struct" && field.parent.typeName !== "google.protobuf.Value") {
        return void 0;
      }
      const messageDesc = field.message;
      let compiled;
      return (value) => {
        if (!(0, guard_js_1.isObject)(value) || (0, is_message_js_1.isMessage)(value, messageDesc)) {
          return value;
        }
        compiled !== null && compiled !== void 0 ? compiled : compiled = compiledCreate(messageDesc);
        return compiled(value);
      };
    }
    function toU8Arr(value) {
      return Array.isArray(value) ? new Uint8Array(value) : value;
    }
    function needsPrototypeChain(desc) {
      switch (desc.file.edition) {
        case EDITION_PROTO3:
          return false;
        case EDITION_PROTO2:
          return true;
        default:
          return desc.fields.some((f) => f.presence != IMPLICIT && f.fieldKind != "message" && !f.oneof);
      }
    }
    function createZeroValue(field) {
      const defaultValue = field.getDefaultValue();
      if (defaultValue !== void 0) {
        return field.fieldKind == "scalar" && field.longAsString ? defaultValue.toString() : defaultValue;
      }
      return field.fieldKind == "scalar" ? (0, scalar_js_1.scalarZeroValue)(field.scalar, field.longAsString) : field.enum.values[0].number;
    }
  }
});

// hardware/generic/goldfish/emulator/ui/aquarium/node_modules/@bufbuild/protobuf/dist/commonjs/reflect/message.js
var require_message = __commonJS({
  "hardware/generic/goldfish/emulator/ui/aquarium/node_modules/@bufbuild/protobuf/dist/commonjs/reflect/message.js"(exports2) {
    "use strict";
    Object.defineProperty(exports2, "__esModule", { value: true });
    exports2.localMessageMapper = localMessageMapper;
    exports2.wktStructToReflect = wktStructToReflect;
    exports2.wktStructToLocal = wktStructToLocal;
    var create_js_1 = require_create();
    var guard_js_1 = require_guard();
    var wrappers_js_1 = require_wrappers();
    var NULL_VALUE = 0;
    function localMessageMapper(field) {
      if (usesJsonRepresentation(field)) {
        return {
          toMessage: (local) => wktStructToReflect(local),
          toLocal: (message) => wktStructToLocal(message)
        };
      }
      if (field.fieldKind == "message" && !field.oneof && (0, wrappers_js_1.isWrapperDesc)(field.message)) {
        const wrapperDesc = field.message;
        const valueLocalName = wrapperDesc.fields[0].localName;
        return {
          toMessage: (local) => {
            const message = (0, create_js_1.create)(wrapperDesc);
            if (local !== void 0) {
              message[valueLocalName] = local;
            }
            return message;
          },
          toLocal: (message) => message[valueLocalName]
        };
      }
      const childDesc = field.message;
      return {
        toMessage: (local) => local === void 0 ? (0, create_js_1.create)(childDesc) : local,
        toLocal: (message) => message
      };
    }
    function usesJsonRepresentation(field) {
      return field.message.typeName == "google.protobuf.Struct" && field.parent.typeName != "google.protobuf.Value";
    }
    function wktStructToReflect(json) {
      const struct = {
        $typeName: "google.protobuf.Struct",
        fields: {}
      };
      if ((0, guard_js_1.isObject)(json)) {
        for (const k of Object.keys(json)) {
          struct.fields[k] = wktValueToReflect(json[k]);
        }
      }
      return struct;
    }
    function wktStructToLocal(val) {
      const json = {};
      for (const k of Object.keys(val.fields)) {
        json[k] = wktValueToLocal(val.fields[k]);
      }
      return json;
    }
    function wktValueToLocal(val) {
      switch (val.kind.case) {
        case "structValue":
          return wktStructToLocal(val.kind.value);
        case "listValue":
          return val.kind.value.values.map(wktValueToLocal);
        case "nullValue":
        case void 0:
          return null;
        default:
          return val.kind.value;
      }
    }
    function wktValueToReflect(json) {
      const value = {
        $typeName: "google.protobuf.Value",
        kind: { case: void 0 }
      };
      switch (typeof json) {
        case "number":
          value.kind = { case: "numberValue", value: json };
          break;
        case "string":
          value.kind = { case: "stringValue", value: json };
          break;
        case "boolean":
          value.kind = { case: "boolValue", value: json };
          break;
        case "object":
          if (json === null) {
            value.kind = { case: "nullValue", value: NULL_VALUE };
          } else if (Array.isArray(json)) {
            const listValue = {
              $typeName: "google.protobuf.ListValue",
              values: []
            };
            if (Array.isArray(json)) {
              for (const e of json) {
                listValue.values.push(wktValueToReflect(e));
              }
            }
            value.kind = {
              case: "listValue",
              value: listValue
            };
          } else {
            value.kind = {
              case: "structValue",
              value: wktStructToReflect(json)
            };
          }
          break;
      }
      return value;
    }
  }
});

// hardware/generic/goldfish/emulator/ui/aquarium/node_modules/@bufbuild/protobuf/dist/commonjs/to-binary.js
var require_to_binary = __commonJS({
  "hardware/generic/goldfish/emulator/ui/aquarium/node_modules/@bufbuild/protobuf/dist/commonjs/to-binary.js"(exports2) {
    "use strict";
    Object.defineProperty(exports2, "__esModule", { value: true });
    exports2.toBinary = toBinary;
    exports2.writeField = writeField;
    var binary_encoding_js_1 = require_binary_encoding();
    var descriptors_js_1 = require_descriptors();
    var error_js_1 = require_error();
    var unsafe_js_1 = require_unsafe();
    var message_js_1 = require_message();
    var proto_int64_js_1 = require_proto_int64();
    var IMPLICIT = 2;
    var LEGACY_REQUIRED = 3;
    var writeDefaults = {
      writeUnknownFields: true
    };
    function makeWriteOptions(options) {
      return options ? Object.assign(Object.assign({}, writeDefaults), options) : writeDefaults;
    }
    function toBinary(schema, message, options) {
      const writer = new binary_encoding_js_1.BinaryWriter();
      compiledWriter(schema)(writer, makeWriteOptions(options), message);
      return writer.finish();
    }
    var compiledWriters = /* @__PURE__ */ new WeakMap();
    function compiledWriter(desc) {
      let compiled = compiledWriters.get(desc);
      if (compiled === void 0) {
        compiled = compileMessage(desc);
      }
      return compiled;
    }
    function compileMessage(desc) {
      const typeName = desc.typeName;
      const sortedFields = desc.fields.concat().sort((a, b) => a.number - b.number);
      const foreignField = sortedFields[0];
      const fieldWriters = [];
      const compiled = (writer, opts, message) => {
        if (message.$typeName !== typeName && foreignField !== void 0) {
          throw new error_js_1.FieldError(foreignField, `cannot use ${foreignField} with message ${message.$typeName}`, "ForeignFieldError");
        }
        for (let i = 0; i < fieldWriters.length; i++) {
          fieldWriters[i](writer, opts, message);
        }
        const unknown = message.$unknown;
        if (unknown !== void 0 && opts.writeUnknownFields) {
          for (let i = 0; i < unknown.length; i++) {
            const { no, wireType, data } = unknown[i];
            writer.tag(no, wireType).raw(data);
          }
        }
      };
      compiledWriters.set(desc, compiled);
      for (const field of sortedFields) {
        fieldWriters.push(compileField(field));
      }
      return compiled;
    }
    function compileField(field) {
      switch (field.fieldKind) {
        case "message":
        case "scalar":
        case "enum":
          return compileSingularField(field);
        case "list":
          return compileListField(field);
        case "map":
          return compileMapField(field);
      }
    }
    function compileSingularField(field) {
      const writeValue = compileSingularValue(field);
      const localName = field.localName;
      if (field.oneof) {
        const oneofLocalName = field.oneof.localName;
        return (writer, opts, message) => {
          const oneof = message[oneofLocalName];
          if (oneof.case === localName) {
            writeValue(writer, opts, oneof.value);
          }
        };
      }
      if (field.presence != IMPLICIT) {
        const requiredError = field.presence == LEGACY_REQUIRED ? `cannot encode ${field} to binary: required field not set` : void 0;
        return (writer, opts, message) => {
          const value = message[localName];
          if (value !== void 0 && Object.prototype.hasOwnProperty.call(message, localName)) {
            writeValue(writer, opts, value);
          } else if (requiredError !== void 0) {
            throw new Error(requiredError);
          }
        };
      }
      if (field.fieldKind == "enum") {
        const zero = field.enum.values[0].number;
        return (writer, opts, message) => {
          const value = message[localName];
          if (value !== zero) {
            writeValue(writer, opts, value);
          }
        };
      }
      switch (field.scalar) {
        case descriptors_js_1.ScalarType.BOOL:
          return (writer, opts, message) => {
            const value = message[localName];
            if (value !== false) {
              writeValue(writer, opts, value);
            }
          };
        case descriptors_js_1.ScalarType.STRING:
          return (writer, opts, message) => {
            const value = message[localName];
            if (value !== "") {
              writeValue(writer, opts, value);
            }
          };
        case descriptors_js_1.ScalarType.BYTES:
          return (writer, opts, message) => {
            const value = message[localName];
            if (!(value instanceof Uint8Array) || value.byteLength > 0) {
              writeValue(writer, opts, value);
            }
          };
        case descriptors_js_1.ScalarType.DOUBLE:
        case descriptors_js_1.ScalarType.FLOAT:
          return (writer, opts, message) => {
            const value = message[localName];
            if (!Object.is(value, 0)) {
              writeValue(writer, opts, value);
            }
          };
        default:
          return (writer, opts, message) => {
            const value = message[localName];
            if (value != 0) {
              writeValue(writer, opts, value);
            }
          };
      }
    }
    function compileSingularValue(field) {
      switch (field.fieldKind) {
        case "message": {
          const { toMessage } = (0, message_js_1.localMessageMapper)(field);
          const writeChild = compileChildWriter(field);
          return (writer, opts, value) => {
            writeChild(writer, opts, toMessage(value));
          };
        }
        case "scalar":
        case "enum": {
          const scalarType = field.fieldKind == "enum" ? descriptors_js_1.ScalarType.INT32 : field.scalar;
          const fieldNo = field.number;
          const wireType = writeTypeOfScalar(scalarType);
          const writeScalar = compileScalarValue(scalarType, field.parent.typeName, field.name);
          return (writer, opts, value) => {
            writer.tag(fieldNo, wireType);
            writeScalar(writer, value);
          };
        }
      }
    }
    function compileListField(field) {
      const localName = field.localName;
      const fieldNo = field.number;
      switch (field.listKind) {
        case "message": {
          const { toMessage } = (0, message_js_1.localMessageMapper)(field);
          const writeChild = compileChildWriter(field);
          return (writer, opts, message) => {
            const items = message[localName];
            for (let i = 0; i < items.length; i++) {
              writeChild(writer, opts, toMessage(items[i]));
            }
          };
        }
        case "scalar":
        case "enum": {
          const scalarType = field.listKind == "enum" ? descriptors_js_1.ScalarType.INT32 : field.scalar;
          const writeScalar = compileScalarValue(scalarType, field.parent.typeName, field.name);
          if (field.packed) {
            return (writer, opts, message) => {
              const items = message[localName];
              if (items.length == 0) {
                return;
              }
              writer.tag(fieldNo, binary_encoding_js_1.WireType.LengthDelimited).fork();
              for (let i = 0; i < items.length; i++) {
                writeScalar(writer, items[i]);
              }
              writer.join();
            };
          }
          const wireType = writeTypeOfScalar(scalarType);
          return (writer, opts, message) => {
            const items = message[localName];
            for (let i = 0; i < items.length; i++) {
              writer.tag(fieldNo, wireType);
              writeScalar(writer, items[i]);
            }
          };
        }
      }
    }
    function compileMapField(field) {
      const localName = field.localName;
      const fieldNo = field.number;
      const writeKey = compileMapKey(field);
      if (field.mapKind == "message") {
        const { toMessage } = (0, message_js_1.localMessageMapper)(field);
        const writeMessage = compiledWriter(field.message);
        return (writer, opts, message) => {
          const record = message[localName];
          const keys = Object.keys(record);
          for (let i = 0; i < keys.length; i++) {
            const key = keys[i];
            writer.tag(fieldNo, binary_encoding_js_1.WireType.LengthDelimited).fork();
            writeKey(writer, key);
            writer.tag(2, binary_encoding_js_1.WireType.LengthDelimited).fork();
            writeMessage(writer, opts, toMessage(record[key]));
            writer.join();
            writer.join();
          }
        };
      }
      const scalarType = field.mapKind == "enum" ? descriptors_js_1.ScalarType.INT32 : field.scalar;
      const valueWireType = writeTypeOfScalar(scalarType);
      const writeScalar = compileScalarValue(scalarType, field.parent.typeName, field.name);
      return (writer, opts, message) => {
        const record = message[localName];
        const keys = Object.keys(record);
        for (let i = 0; i < keys.length; i++) {
          const key = keys[i];
          writer.tag(fieldNo, binary_encoding_js_1.WireType.LengthDelimited).fork();
          writeKey(writer, key);
          writer.tag(2, valueWireType);
          writeScalar(writer, record[key]);
          writer.join();
        }
      };
    }
    function compileMapKey(field) {
      const wireType = writeTypeOfScalar(field.mapKey);
      const writeScalar = compileScalarValue(field.mapKey, field.parent.typeName, field.name);
      const convertKey = compileMapKeyConverter(field.mapKey);
      return (writer, key) => {
        writer.tag(1, wireType);
        writeScalar(writer, convertKey(key));
      };
    }
    function compileMapKeyConverter(type) {
      switch (type) {
        case descriptors_js_1.ScalarType.STRING:
          return (key) => key;
        case descriptors_js_1.ScalarType.BOOL:
          return (key) => key === "true" ? true : key === "false" ? false : key;
        case descriptors_js_1.ScalarType.UINT64:
        case descriptors_js_1.ScalarType.FIXED64:
          return (key) => {
            try {
              return proto_int64_js_1.protoInt64.uParse(key);
            } catch (_a) {
              return key;
            }
          };
        case descriptors_js_1.ScalarType.INT64:
        case descriptors_js_1.ScalarType.SFIXED64:
        case descriptors_js_1.ScalarType.SINT64:
          return (key) => {
            try {
              return proto_int64_js_1.protoInt64.parse(key);
            } catch (_a) {
              return key;
            }
          };
        default:
          return (key) => {
            const n = Number.parseInt(key);
            return Number.isFinite(n) ? n : key;
          };
      }
    }
    function compileScalarValue(type, messageName, fieldName) {
      const writeScalar = compileScalarWrite(type);
      return (writer, value) => {
        try {
          writeScalar(writer, value);
        } catch (e) {
          if (e instanceof Error) {
            throw new Error(`cannot encode field ${messageName}.${fieldName} to binary: ${e.message}`);
          }
          throw e;
        }
      };
    }
    function compileScalarWrite(type) {
      switch (type) {
        case descriptors_js_1.ScalarType.STRING:
          return (writer, value) => writer.string(value);
        case descriptors_js_1.ScalarType.BOOL:
          return (writer, value) => writer.bool(value);
        case descriptors_js_1.ScalarType.DOUBLE:
          return (writer, value) => writer.double(value);
        case descriptors_js_1.ScalarType.FLOAT:
          return (writer, value) => writer.float(value);
        case descriptors_js_1.ScalarType.INT32:
          return (writer, value) => writer.int32(value);
        case descriptors_js_1.ScalarType.INT64:
          return (writer, value) => writer.int64(value);
        case descriptors_js_1.ScalarType.UINT64:
          return (writer, value) => writer.uint64(value);
        case descriptors_js_1.ScalarType.FIXED64:
          return (writer, value) => writer.fixed64(value);
        case descriptors_js_1.ScalarType.BYTES:
          return (writer, value) => writer.bytes(value);
        case descriptors_js_1.ScalarType.FIXED32:
          return (writer, value) => writer.fixed32(value);
        case descriptors_js_1.ScalarType.SFIXED32:
          return (writer, value) => writer.sfixed32(value);
        case descriptors_js_1.ScalarType.SFIXED64:
          return (writer, value) => writer.sfixed64(value);
        case descriptors_js_1.ScalarType.SINT64:
          return (writer, value) => writer.sint64(value);
        case descriptors_js_1.ScalarType.UINT32:
          return (writer, value) => writer.uint32(value);
        case descriptors_js_1.ScalarType.SINT32:
          return (writer, value) => writer.sint32(value);
      }
    }
    function writeField(writer, opts, msg, field) {
      compileField(field)(writer, opts, msg[unsafe_js_1.unsafeLocal]);
    }
    function compileChildWriter(field) {
      const fieldNo = field.number;
      const writeMessage = compiledWriter(field.message);
      if (field.delimitedEncoding) {
        return (writer, opts, child) => {
          writer.tag(fieldNo, binary_encoding_js_1.WireType.StartGroup);
          writeMessage(writer, opts, child);
          writer.tag(fieldNo, binary_encoding_js_1.WireType.EndGroup);
        };
      }
      return (writer, opts, child) => {
        writer.tag(fieldNo, binary_encoding_js_1.WireType.LengthDelimited).fork();
        writeMessage(writer, opts, child);
        writer.join();
      };
    }
    function writeTypeOfScalar(type) {
      switch (type) {
        case descriptors_js_1.ScalarType.BYTES:
        case descriptors_js_1.ScalarType.STRING:
          return binary_encoding_js_1.WireType.LengthDelimited;
        case descriptors_js_1.ScalarType.DOUBLE:
        case descriptors_js_1.ScalarType.FIXED64:
        case descriptors_js_1.ScalarType.SFIXED64:
          return binary_encoding_js_1.WireType.Bit64;
        case descriptors_js_1.ScalarType.FIXED32:
        case descriptors_js_1.ScalarType.SFIXED32:
        case descriptors_js_1.ScalarType.FLOAT:
          return binary_encoding_js_1.WireType.Bit32;
        default:
          return binary_encoding_js_1.WireType.Varint;
      }
    }
  }
});

// hardware/generic/goldfish/emulator/ui/aquarium/node_modules/@bufbuild/protobuf/dist/commonjs/from-binary.js
var require_from_binary = __commonJS({
  "hardware/generic/goldfish/emulator/ui/aquarium/node_modules/@bufbuild/protobuf/dist/commonjs/from-binary.js"(exports2) {
    "use strict";
    Object.defineProperty(exports2, "__esModule", { value: true });
    exports2.makeReadContext = makeReadContext;
    exports2.fromBinary = fromBinary;
    exports2.mergeFromBinary = mergeFromBinary;
    exports2.readField = readField;
    var descriptors_js_1 = require_descriptors();
    var scalar_js_1 = require_scalar();
    var error_js_1 = require_error();
    var unsafe_js_1 = require_unsafe();
    var message_js_1 = require_message();
    var create_js_1 = require_create();
    var binary_encoding_js_1 = require_binary_encoding();
    var varint_js_1 = require_varint();
    function makeReadContext(options) {
      return Object.assign(Object.assign({ readUnknownFields: true, recursionLimit: 100 }, options), { depth: 0 });
    }
    function fromBinary(schema, bytes, options) {
      const message = (0, create_js_1.create)(schema);
      compiledReader(schema).read(message, new binary_encoding_js_1.BinaryReader(bytes), makeReadContext(options), bytes.byteLength);
      return message;
    }
    function mergeFromBinary(schema, target, bytes, options) {
      if (target.$typeName !== schema.typeName && schema.fields.length > 0) {
        throw new error_js_1.FieldError(schema.fields[0], `cannot use ${schema.fields[0]} with message ${target.$typeName}`, "ForeignFieldError");
      }
      compiledReader(schema).read(target, new binary_encoding_js_1.BinaryReader(bytes), makeReadContext(options), bytes.byteLength);
      return target;
    }
    var compiledReaders = /* @__PURE__ */ new WeakMap();
    function compiledReader(desc) {
      let compiled = compiledReaders.get(desc);
      if (compiled === void 0) {
        compiled = compileMessage(desc);
      }
      return compiled;
    }
    function compileMessage(desc) {
      const descString = String(desc);
      const fieldReaders = /* @__PURE__ */ new Map();
      const compiled = {
        read: compileMessageReader(descString, fieldReaders),
        readGroup: compileGroupReader(descString, fieldReaders)
      };
      compiledReaders.set(desc, compiled);
      for (const field of desc.fields) {
        fieldReaders.set(field.number, compileFieldReader(field));
      }
      return compiled;
    }
    function compileMessageReader(descString, fieldReaders) {
      return (message, reader, ctx, length) => {
        var _a;
        if (++ctx.depth > ctx.recursionLimit) {
          throw new Error(`cannot decode ${descString} from binary: maximum recursion depth of ${ctx.recursionLimit} reached`);
        }
        const end = reader.pos + length;
        const unknownFields = (_a = message.$unknown) !== null && _a !== void 0 ? _a : [];
        while (reader.pos < end) {
          const [fieldNo, wireType] = reader.tag();
          const fieldReader = fieldReaders.get(fieldNo);
          if (fieldReader === void 0) {
            const data = reader.skip(wireType, fieldNo, ctx.recursionLimit - ctx.depth);
            if (ctx.readUnknownFields) {
              unknownFields.push({ no: fieldNo, wireType, data });
            }
            continue;
          }
          fieldReader(message, reader, ctx, wireType);
        }
        if (unknownFields.length > 0) {
          message.$unknown = unknownFields;
        }
        ctx.depth--;
      };
    }
    function compileGroupReader(descString, fieldReaders) {
      return (message, reader, ctx, fieldNo) => {
        var _a;
        if (++ctx.depth > ctx.recursionLimit) {
          throw new Error(`cannot decode ${descString} from binary: maximum recursion depth of ${ctx.recursionLimit} reached`);
        }
        let recordFieldNo;
        let wireType;
        const unknownFields = (_a = message.$unknown) !== null && _a !== void 0 ? _a : [];
        while (reader.pos < reader.len) {
          [recordFieldNo, wireType] = reader.tag();
          if (wireType == binary_encoding_js_1.WireType.EndGroup) {
            break;
          }
          const fieldReader = fieldReaders.get(recordFieldNo);
          if (fieldReader === void 0) {
            const data = reader.skip(wireType, recordFieldNo, ctx.recursionLimit - ctx.depth);
            if (ctx.readUnknownFields) {
              unknownFields.push({ no: recordFieldNo, wireType, data });
            }
            continue;
          }
          fieldReader(message, reader, ctx, wireType);
        }
        if (wireType != binary_encoding_js_1.WireType.EndGroup || recordFieldNo !== fieldNo) {
          throw new Error("invalid end group tag");
        }
        if (unknownFields.length > 0) {
          message.$unknown = unknownFields;
        }
        ctx.depth--;
      };
    }
    function readField(message, reader, field, wireType, ctx) {
      compileFieldReader(field)(message[unsafe_js_1.unsafeLocal], reader, ctx, wireType);
    }
    function compileFieldReader(field) {
      switch (field.fieldKind) {
        case "scalar":
          return compileScalarFieldReader(field);
        case "enum":
          return compileEnumFieldReader(field);
        case "message":
          return compileMessageFieldReader(field);
        case "list":
          return compileListFieldReader(field);
        case "map":
          return compileMapFieldReader(field);
      }
    }
    function compileScalarFieldReader(field) {
      const readScalar = compileScalarReader(field.scalar, field.utf8Validation, field.longAsString);
      const localName = field.localName;
      if (field.oneof) {
        const oneofLocalName = field.oneof.localName;
        return (message, reader) => {
          message[oneofLocalName] = {
            case: localName,
            value: readScalar(reader)
          };
        };
      }
      return (message, reader) => {
        message[localName] = readScalar(reader);
      };
    }
    function compileEnumFieldReader(field) {
      var _a;
      const localName = field.localName;
      const oneofLocalName = (_a = field.oneof) === null || _a === void 0 ? void 0 : _a.localName;
      if (field.enum.open) {
        if (oneofLocalName !== void 0) {
          return (message, reader) => {
            message[oneofLocalName] = { case: localName, value: reader.int32() };
          };
        }
        return (message, reader) => {
          message[localName] = reader.int32();
        };
      }
      const values = field.enum.values;
      const fieldNo = field.number;
      return (message, reader, ctx, wireType) => {
        var _a2;
        const val = reader.int32();
        if (values.some((v) => v.number === val)) {
          if (oneofLocalName !== void 0) {
            message[oneofLocalName] = { case: localName, value: val };
          } else {
            message[localName] = val;
          }
        } else if (ctx.readUnknownFields) {
          const bytes = [];
          (0, varint_js_1.varint32write)(val, bytes);
          const unknownFields = (_a2 = message.$unknown) !== null && _a2 !== void 0 ? _a2 : [];
          unknownFields.push({
            no: fieldNo,
            wireType,
            data: new Uint8Array(bytes)
          });
          message.$unknown = unknownFields;
        }
      };
    }
    function compileMessageFieldReader(field) {
      const localName = field.localName;
      const { toMessage, toLocal } = (0, message_js_1.localMessageMapper)(field);
      const readChild = compileChildReader(field);
      if (field.oneof) {
        const oneofLocalName = field.oneof.localName;
        return (message, reader, ctx) => {
          const oneof = message[oneofLocalName];
          const child = toMessage(oneof.case === localName ? oneof.value : void 0);
          readChild(child, reader, ctx);
          message[oneofLocalName] = { case: localName, value: toLocal(child) };
        };
      }
      return (message, reader, ctx) => {
        const child = toMessage(message[localName]);
        readChild(child, reader, ctx);
        message[localName] = toLocal(child);
      };
    }
    function compileChildReader(field) {
      const compiledChild = compiledReader(field.message);
      if (field.delimitedEncoding) {
        const fieldNo = field.number;
        return (child, reader, ctx) => compiledChild.readGroup(child, reader, ctx, fieldNo);
      }
      return (child, reader, ctx) => compiledChild.read(child, reader, ctx, reader.uint32());
    }
    function compileListFieldReader(field) {
      const localName = field.localName;
      if (field.listKind == "message") {
        const { toMessage, toLocal } = (0, message_js_1.localMessageMapper)(field);
        const readChild = compileChildReader(field);
        return (message, reader, ctx) => {
          const child = toMessage(void 0);
          readChild(child, reader, ctx);
          message[localName].push(toLocal(child));
        };
      }
      const scalarType = field.listKind == "enum" ? descriptors_js_1.ScalarType.INT32 : field.scalar;
      const longAsString = field.listKind == "scalar" ? field.longAsString : false;
      const readScalar = compileScalarReader(scalarType, field.utf8Validation, longAsString);
      const packedPossible = scalarType != descriptors_js_1.ScalarType.STRING && scalarType != descriptors_js_1.ScalarType.BYTES;
      return (message, reader, ctx, wireType) => {
        const items = message[localName];
        if (wireType == binary_encoding_js_1.WireType.LengthDelimited && packedPossible) {
          const end = reader.uint32() + reader.pos;
          while (reader.pos < end) {
            items.push(readScalar(reader));
          }
        } else {
          items.push(readScalar(reader));
        }
      };
    }
    function compileMapFieldReader(field) {
      const localName = field.localName;
      const readKey = compileScalarReader(field.mapKey, field.utf8Validation, false);
      const keyZero = (0, scalar_js_1.scalarZeroValue)(field.mapKey, false);
      let readValue;
      let valueDefault;
      switch (field.mapKind) {
        case "scalar": {
          const scalar = field.scalar;
          const readScalar = compileScalarReader(scalar, field.utf8Validation, false);
          readValue = (reader) => readScalar(reader);
          if (scalar == descriptors_js_1.ScalarType.BYTES) {
            valueDefault = () => new Uint8Array(0);
          } else {
            const zero = (0, scalar_js_1.scalarZeroValue)(scalar, false);
            valueDefault = () => zero;
          }
          break;
        }
        case "enum": {
          const zero = field.enum.values[0].number;
          readValue = (reader) => reader.int32();
          valueDefault = () => zero;
          break;
        }
        case "message": {
          const { toMessage, toLocal } = (0, message_js_1.localMessageMapper)(field);
          const readChild = compiledReader(field.message).read;
          readValue = (reader, ctx) => {
            const child = toMessage(void 0);
            readChild(child, reader, ctx, reader.uint32());
            return toLocal(child);
          };
          valueDefault = () => toLocal(toMessage(void 0));
          break;
        }
      }
      return (message, reader, ctx) => {
        const record = message[localName];
        let key;
        let val;
        const len = reader.uint32();
        const end = reader.pos + len;
        while (reader.pos < end) {
          const [fieldNo] = reader.tag();
          switch (fieldNo) {
            case 1:
              key = readKey(reader);
              break;
            case 2:
              val = readValue(reader, ctx);
              break;
          }
        }
        if (key === void 0) {
          key = keyZero;
        }
        if (val === void 0) {
          val = valueDefault();
        }
        record[key] = val;
      };
    }
    function compileScalarReader(type, utf8Validation, longAsString) {
      switch (type) {
        case descriptors_js_1.ScalarType.STRING:
          return (reader) => reader.string(utf8Validation);
        case descriptors_js_1.ScalarType.BOOL:
          return (reader) => reader.bool();
        case descriptors_js_1.ScalarType.DOUBLE:
          return (reader) => reader.double();
        case descriptors_js_1.ScalarType.FLOAT:
          return (reader) => reader.float();
        case descriptors_js_1.ScalarType.INT32:
          return (reader) => reader.int32();
        case descriptors_js_1.ScalarType.INT64:
          if (longAsString) {
            return (reader) => String(reader.int64());
          }
          return (reader) => reader.int64();
        case descriptors_js_1.ScalarType.UINT64:
          if (longAsString) {
            return (reader) => String(reader.uint64());
          }
          return (reader) => reader.uint64();
        case descriptors_js_1.ScalarType.FIXED64:
          if (longAsString) {
            return (reader) => String(reader.fixed64());
          }
          return (reader) => reader.fixed64();
        case descriptors_js_1.ScalarType.BYTES:
          return (reader) => reader.bytes();
        case descriptors_js_1.ScalarType.FIXED32:
          return (reader) => reader.fixed32();
        case descriptors_js_1.ScalarType.SFIXED32:
          return (reader) => reader.sfixed32();
        case descriptors_js_1.ScalarType.SFIXED64:
          if (longAsString) {
            return (reader) => String(reader.sfixed64());
          }
          return (reader) => reader.sfixed64();
        case descriptors_js_1.ScalarType.SINT64:
          if (longAsString) {
            return (reader) => String(reader.sint64());
          }
          return (reader) => reader.sint64();
        case descriptors_js_1.ScalarType.UINT32:
          return (reader) => reader.uint32();
        case descriptors_js_1.ScalarType.SINT32:
          return (reader) => reader.sint32();
      }
    }
  }
});

// hardware/generic/goldfish/emulator/ui/aquarium/node_modules/@bufbuild/protobuf/dist/commonjs/wire/size-delimited.js
var require_size_delimited = __commonJS({
  "hardware/generic/goldfish/emulator/ui/aquarium/node_modules/@bufbuild/protobuf/dist/commonjs/wire/size-delimited.js"(exports2) {
    "use strict";
    var __asyncValues = exports2 && exports2.__asyncValues || function(o) {
      if (!Symbol.asyncIterator) throw new TypeError("Symbol.asyncIterator is not defined.");
      var m = o[Symbol.asyncIterator], i;
      return m ? m.call(o) : (o = typeof __values === "function" ? __values(o) : o[Symbol.iterator](), i = {}, verb("next"), verb("throw"), verb("return"), i[Symbol.asyncIterator] = function() {
        return this;
      }, i);
      function verb(n) {
        i[n] = o[n] && function(v) {
          return new Promise(function(resolve, reject) {
            v = o[n](v), settle(resolve, reject, v.done, v.value);
          });
        };
      }
      function settle(resolve, reject, d, v) {
        Promise.resolve(v).then(function(v2) {
          resolve({ value: v2, done: d });
        }, reject);
      }
    };
    var __await = exports2 && exports2.__await || function(v) {
      return this instanceof __await ? (this.v = v, this) : new __await(v);
    };
    var __asyncGenerator = exports2 && exports2.__asyncGenerator || function(thisArg, _arguments, generator) {
      if (!Symbol.asyncIterator) throw new TypeError("Symbol.asyncIterator is not defined.");
      var g = generator.apply(thisArg, _arguments || []), i, q = [];
      return i = Object.create((typeof AsyncIterator === "function" ? AsyncIterator : Object).prototype), verb("next"), verb("throw"), verb("return", awaitReturn), i[Symbol.asyncIterator] = function() {
        return this;
      }, i;
      function awaitReturn(f) {
        return function(v) {
          return Promise.resolve(v).then(f, reject);
        };
      }
      function verb(n, f) {
        if (g[n]) {
          i[n] = function(v) {
            return new Promise(function(a, b) {
              q.push([n, v, a, b]) > 1 || resume(n, v);
            });
          };
          if (f) i[n] = f(i[n]);
        }
      }
      function resume(n, v) {
        try {
          step(g[n](v));
        } catch (e) {
          settle(q[0][3], e);
        }
      }
      function step(r) {
        r.value instanceof __await ? Promise.resolve(r.value.v).then(fulfill, reject) : settle(q[0][2], r);
      }
      function fulfill(value) {
        resume("next", value);
      }
      function reject(value) {
        resume("throw", value);
      }
      function settle(f, v) {
        if (f(v), q.shift(), q.length) resume(q[0][0], q[0][1]);
      }
    };
    Object.defineProperty(exports2, "__esModule", { value: true });
    exports2.sizeDelimitedEncode = sizeDelimitedEncode;
    exports2.sizeDelimitedDecodeStream = sizeDelimitedDecodeStream;
    exports2.sizeDelimitedPeek = sizeDelimitedPeek;
    var to_binary_js_1 = require_to_binary();
    var binary_encoding_js_1 = require_binary_encoding();
    var from_binary_js_1 = require_from_binary();
    function sizeDelimitedEncode(messageDesc, message, options) {
      const writer = new binary_encoding_js_1.BinaryWriter();
      writer.bytes((0, to_binary_js_1.toBinary)(messageDesc, message, options));
      return writer.finish();
    }
    var defaultReadMaxBytes = 64 * 1024 * 1024;
    var ByteBuffer = class {
      constructor() {
        this.buffer = new Uint8Array(0);
        this.length = 0;
      }
      get byteLength() {
        return this.length;
      }
      bytes() {
        return this.buffer.subarray(0, this.length);
      }
      append(chunk) {
        const newByteLength = this.length + chunk.byteLength;
        if (newByteLength > this.buffer.byteLength) {
          const grown = new Uint8Array(Math.max(this.buffer.byteLength * 2, newByteLength));
          grown.set(this.buffer.subarray(0, this.length));
          this.buffer = grown;
        }
        this.buffer.set(chunk, this.length);
        this.length += chunk.byteLength;
      }
    };
    function sizeDelimitedDecodeStream(messageDesc, iterable, options) {
      return __asyncGenerator(this, arguments, function* sizeDelimitedDecodeStream_1() {
        var _a, e_1, _b, _c;
        var _d;
        const readMaxBytes = (_d = options === null || options === void 0 ? void 0 : options.readMaxBytes) !== null && _d !== void 0 ? _d : defaultReadMaxBytes;
        let buffer = new ByteBuffer();
        try {
          for (var _e = true, iterable_1 = __asyncValues(iterable), iterable_1_1; iterable_1_1 = yield __await(iterable_1.next()), _a = iterable_1_1.done, !_a; _e = true) {
            _c = iterable_1_1.value;
            _e = false;
            const chunk = _c;
            buffer.append(chunk);
            const bytes = buffer.bytes();
            let offset = 0;
            for (; ; ) {
              const size = sizeDelimitedPeek(bytes.subarray(offset));
              if (size.eof) {
                break;
              }
              if (size.size > readMaxBytes) {
                throw new Error(`message size ${size.size} is larger than configured readMaxBytes ${readMaxBytes}`);
              }
              const messageStart = offset + size.offset;
              const messageEnd = messageStart + size.size;
              if (messageEnd > bytes.byteLength) {
                break;
              }
              yield yield __await((0, from_binary_js_1.fromBinary)(messageDesc, bytes.subarray(messageStart, messageEnd), options));
              offset = messageEnd;
            }
            if (offset > 0) {
              buffer = new ByteBuffer();
              buffer.append(bytes.subarray(offset));
            }
          }
        } catch (e_1_1) {
          e_1 = { error: e_1_1 };
        } finally {
          try {
            if (!_e && !_a && (_b = iterable_1.return)) yield __await(_b.call(iterable_1));
          } finally {
            if (e_1) throw e_1.error;
          }
        }
        if (buffer.byteLength > 0) {
          throw new Error("incomplete data");
        }
      });
    }
    function sizeDelimitedPeek(data) {
      const sizeEof = { eof: true, size: null, offset: null };
      for (let i = 0; i < 10; i++) {
        if (i > data.byteLength) {
          return sizeEof;
        }
        if ((data[i] & 128) == 0) {
          const reader = new binary_encoding_js_1.BinaryReader(data);
          let size;
          try {
            size = reader.uint32();
          } catch (e) {
            if (e instanceof RangeError) {
              return sizeEof;
            }
            throw e;
          }
          return {
            eof: false,
            size,
            offset: reader.pos
          };
        }
      }
      throw new Error("invalid varint");
    }
  }
});

// hardware/generic/goldfish/emulator/ui/aquarium/node_modules/@bufbuild/protobuf/dist/commonjs/wire/index.js
var require_wire = __commonJS({
  "hardware/generic/goldfish/emulator/ui/aquarium/node_modules/@bufbuild/protobuf/dist/commonjs/wire/index.js"(exports2) {
    "use strict";
    var __createBinding = exports2 && exports2.__createBinding || (Object.create ? function(o, m, k, k2) {
      if (k2 === void 0) k2 = k;
      var desc = Object.getOwnPropertyDescriptor(m, k);
      if (!desc || ("get" in desc ? !m.__esModule : desc.writable || desc.configurable)) {
        desc = { enumerable: true, get: function() {
          return m[k];
        } };
      }
      Object.defineProperty(o, k2, desc);
    } : function(o, m, k, k2) {
      if (k2 === void 0) k2 = k;
      o[k2] = m[k];
    });
    var __exportStar = exports2 && exports2.__exportStar || function(m, exports3) {
      for (var p in m) if (p !== "default" && !Object.prototype.hasOwnProperty.call(exports3, p)) __createBinding(exports3, m, p);
    };
    Object.defineProperty(exports2, "__esModule", { value: true });
    exports2.configureTextEncoding = exports2.getTextEncoding = void 0;
    __exportStar(require_binary_encoding(), exports2);
    __exportStar(require_base64_encoding(), exports2);
    var text_encoding_js_1 = require_text_encoding();
    Object.defineProperty(exports2, "getTextEncoding", { enumerable: true, get: function() {
      return text_encoding_js_1.getTextEncoding;
    } });
    Object.defineProperty(exports2, "configureTextEncoding", { enumerable: true, get: function() {
      return text_encoding_js_1.configureTextEncoding;
    } });
    __exportStar(require_text_format(), exports2);
    __exportStar(require_size_delimited(), exports2);
  }
});

// hardware/generic/goldfish/emulator/ui/aquarium/node_modules/ts-proto-descriptors/dist/google/protobuf/descriptor.js
var require_descriptor = __commonJS({
  "hardware/generic/goldfish/emulator/ui/aquarium/node_modules/ts-proto-descriptors/dist/google/protobuf/descriptor.js"(exports2) {
    "use strict";
    Object.defineProperty(exports2, "__esModule", { value: true });
    exports2.featureSet_JsonFormatFromJSON = exports2.FeatureSet_JsonFormat = exports2.featureSet_MessageEncodingToJSON = exports2.featureSet_MessageEncodingFromJSON = exports2.FeatureSet_MessageEncoding = exports2.featureSet_Utf8ValidationToJSON = exports2.featureSet_Utf8ValidationFromJSON = exports2.FeatureSet_Utf8Validation = exports2.featureSet_RepeatedFieldEncodingToJSON = exports2.featureSet_RepeatedFieldEncodingFromJSON = exports2.FeatureSet_RepeatedFieldEncoding = exports2.featureSet_EnumTypeToJSON = exports2.featureSet_EnumTypeFromJSON = exports2.FeatureSet_EnumType = exports2.featureSet_FieldPresenceToJSON = exports2.featureSet_FieldPresenceFromJSON = exports2.FeatureSet_FieldPresence = exports2.methodOptions_IdempotencyLevelToJSON = exports2.methodOptions_IdempotencyLevelFromJSON = exports2.MethodOptions_IdempotencyLevel = exports2.fieldOptions_OptionTargetTypeToJSON = exports2.fieldOptions_OptionTargetTypeFromJSON = exports2.FieldOptions_OptionTargetType = exports2.fieldOptions_OptionRetentionToJSON = exports2.fieldOptions_OptionRetentionFromJSON = exports2.FieldOptions_OptionRetention = exports2.fieldOptions_JSTypeToJSON = exports2.fieldOptions_JSTypeFromJSON = exports2.FieldOptions_JSType = exports2.fieldOptions_CTypeToJSON = exports2.fieldOptions_CTypeFromJSON = exports2.FieldOptions_CType = exports2.fileOptions_OptimizeModeToJSON = exports2.fileOptions_OptimizeModeFromJSON = exports2.FileOptions_OptimizeMode = exports2.fieldDescriptorProto_LabelToJSON = exports2.fieldDescriptorProto_LabelFromJSON = exports2.FieldDescriptorProto_Label = exports2.fieldDescriptorProto_TypeToJSON = exports2.fieldDescriptorProto_TypeFromJSON = exports2.FieldDescriptorProto_Type = exports2.extensionRangeOptions_VerificationStateToJSON = exports2.extensionRangeOptions_VerificationStateFromJSON = exports2.ExtensionRangeOptions_VerificationState = exports2.symbolVisibilityToJSON = exports2.symbolVisibilityFromJSON = exports2.SymbolVisibility = exports2.editionToJSON = exports2.editionFromJSON = exports2.Edition = void 0;
    exports2.GeneratedCodeInfo_Annotation = exports2.GeneratedCodeInfo = exports2.SourceCodeInfo_Location = exports2.SourceCodeInfo = exports2.FeatureSetDefaults_FeatureSetEditionDefault = exports2.FeatureSetDefaults = exports2.FeatureSet_VisibilityFeature = exports2.FeatureSet = exports2.UninterpretedOption_NamePart = exports2.UninterpretedOption = exports2.MethodOptions = exports2.ServiceOptions = exports2.EnumValueOptions = exports2.EnumOptions = exports2.OneofOptions = exports2.FieldOptions_FeatureSupport = exports2.FieldOptions_EditionDefault = exports2.FieldOptions = exports2.MessageOptions = exports2.FileOptions = exports2.MethodDescriptorProto = exports2.ServiceDescriptorProto = exports2.EnumValueDescriptorProto = exports2.EnumDescriptorProto_EnumReservedRange = exports2.EnumDescriptorProto = exports2.OneofDescriptorProto = exports2.FieldDescriptorProto = exports2.ExtensionRangeOptions_Declaration = exports2.ExtensionRangeOptions = exports2.DescriptorProto_ReservedRange = exports2.DescriptorProto_ExtensionRange = exports2.DescriptorProto = exports2.FileDescriptorProto = exports2.FileDescriptorSet = exports2.generatedCodeInfo_Annotation_SemanticToJSON = exports2.generatedCodeInfo_Annotation_SemanticFromJSON = exports2.GeneratedCodeInfo_Annotation_Semantic = exports2.featureSet_VisibilityFeature_DefaultSymbolVisibilityToJSON = exports2.featureSet_VisibilityFeature_DefaultSymbolVisibilityFromJSON = exports2.FeatureSet_VisibilityFeature_DefaultSymbolVisibility = exports2.featureSet_EnforceNamingStyleToJSON = exports2.featureSet_EnforceNamingStyleFromJSON = exports2.FeatureSet_EnforceNamingStyle = exports2.featureSet_JsonFormatToJSON = void 0;
    var wire_1 = require_wire();
    var Edition;
    (function(Edition2) {
      Edition2[Edition2["EDITION_UNKNOWN"] = 0] = "EDITION_UNKNOWN";
      Edition2[Edition2["EDITION_LEGACY"] = 900] = "EDITION_LEGACY";
      Edition2[Edition2["EDITION_PROTO2"] = 998] = "EDITION_PROTO2";
      Edition2[Edition2["EDITION_PROTO3"] = 999] = "EDITION_PROTO3";
      Edition2[Edition2["EDITION_2023"] = 1e3] = "EDITION_2023";
      Edition2[Edition2["EDITION_2024"] = 1001] = "EDITION_2024";
      Edition2[Edition2["EDITION_UNSTABLE"] = 9999] = "EDITION_UNSTABLE";
      Edition2[Edition2["EDITION_1_TEST_ONLY"] = 1] = "EDITION_1_TEST_ONLY";
      Edition2[Edition2["EDITION_2_TEST_ONLY"] = 2] = "EDITION_2_TEST_ONLY";
      Edition2[Edition2["EDITION_99997_TEST_ONLY"] = 99997] = "EDITION_99997_TEST_ONLY";
      Edition2[Edition2["EDITION_99998_TEST_ONLY"] = 99998] = "EDITION_99998_TEST_ONLY";
      Edition2[Edition2["EDITION_99999_TEST_ONLY"] = 99999] = "EDITION_99999_TEST_ONLY";
      Edition2[Edition2["EDITION_MAX"] = 2147483647] = "EDITION_MAX";
      Edition2[Edition2["UNRECOGNIZED"] = -1] = "UNRECOGNIZED";
    })(Edition || (exports2.Edition = Edition = {}));
    function editionFromJSON(object) {
      switch (object) {
        case 0:
        case "EDITION_UNKNOWN":
          return Edition.EDITION_UNKNOWN;
        case 900:
        case "EDITION_LEGACY":
          return Edition.EDITION_LEGACY;
        case 998:
        case "EDITION_PROTO2":
          return Edition.EDITION_PROTO2;
        case 999:
        case "EDITION_PROTO3":
          return Edition.EDITION_PROTO3;
        case 1e3:
        case "EDITION_2023":
          return Edition.EDITION_2023;
        case 1001:
        case "EDITION_2024":
          return Edition.EDITION_2024;
        case 9999:
        case "EDITION_UNSTABLE":
          return Edition.EDITION_UNSTABLE;
        case 1:
        case "EDITION_1_TEST_ONLY":
          return Edition.EDITION_1_TEST_ONLY;
        case 2:
        case "EDITION_2_TEST_ONLY":
          return Edition.EDITION_2_TEST_ONLY;
        case 99997:
        case "EDITION_99997_TEST_ONLY":
          return Edition.EDITION_99997_TEST_ONLY;
        case 99998:
        case "EDITION_99998_TEST_ONLY":
          return Edition.EDITION_99998_TEST_ONLY;
        case 99999:
        case "EDITION_99999_TEST_ONLY":
          return Edition.EDITION_99999_TEST_ONLY;
        case 2147483647:
        case "EDITION_MAX":
          return Edition.EDITION_MAX;
        case -1:
        case "UNRECOGNIZED":
        default:
          return Edition.UNRECOGNIZED;
      }
    }
    exports2.editionFromJSON = editionFromJSON;
    function editionToJSON(object) {
      switch (object) {
        case Edition.EDITION_UNKNOWN:
          return "EDITION_UNKNOWN";
        case Edition.EDITION_LEGACY:
          return "EDITION_LEGACY";
        case Edition.EDITION_PROTO2:
          return "EDITION_PROTO2";
        case Edition.EDITION_PROTO3:
          return "EDITION_PROTO3";
        case Edition.EDITION_2023:
          return "EDITION_2023";
        case Edition.EDITION_2024:
          return "EDITION_2024";
        case Edition.EDITION_UNSTABLE:
          return "EDITION_UNSTABLE";
        case Edition.EDITION_1_TEST_ONLY:
          return "EDITION_1_TEST_ONLY";
        case Edition.EDITION_2_TEST_ONLY:
          return "EDITION_2_TEST_ONLY";
        case Edition.EDITION_99997_TEST_ONLY:
          return "EDITION_99997_TEST_ONLY";
        case Edition.EDITION_99998_TEST_ONLY:
          return "EDITION_99998_TEST_ONLY";
        case Edition.EDITION_99999_TEST_ONLY:
          return "EDITION_99999_TEST_ONLY";
        case Edition.EDITION_MAX:
          return "EDITION_MAX";
        case Edition.UNRECOGNIZED:
        default:
          return "UNRECOGNIZED";
      }
    }
    exports2.editionToJSON = editionToJSON;
    var SymbolVisibility;
    (function(SymbolVisibility2) {
      SymbolVisibility2[SymbolVisibility2["VISIBILITY_UNSET"] = 0] = "VISIBILITY_UNSET";
      SymbolVisibility2[SymbolVisibility2["VISIBILITY_LOCAL"] = 1] = "VISIBILITY_LOCAL";
      SymbolVisibility2[SymbolVisibility2["VISIBILITY_EXPORT"] = 2] = "VISIBILITY_EXPORT";
      SymbolVisibility2[SymbolVisibility2["UNRECOGNIZED"] = -1] = "UNRECOGNIZED";
    })(SymbolVisibility || (exports2.SymbolVisibility = SymbolVisibility = {}));
    function symbolVisibilityFromJSON(object) {
      switch (object) {
        case 0:
        case "VISIBILITY_UNSET":
          return SymbolVisibility.VISIBILITY_UNSET;
        case 1:
        case "VISIBILITY_LOCAL":
          return SymbolVisibility.VISIBILITY_LOCAL;
        case 2:
        case "VISIBILITY_EXPORT":
          return SymbolVisibility.VISIBILITY_EXPORT;
        case -1:
        case "UNRECOGNIZED":
        default:
          return SymbolVisibility.UNRECOGNIZED;
      }
    }
    exports2.symbolVisibilityFromJSON = symbolVisibilityFromJSON;
    function symbolVisibilityToJSON(object) {
      switch (object) {
        case SymbolVisibility.VISIBILITY_UNSET:
          return "VISIBILITY_UNSET";
        case SymbolVisibility.VISIBILITY_LOCAL:
          return "VISIBILITY_LOCAL";
        case SymbolVisibility.VISIBILITY_EXPORT:
          return "VISIBILITY_EXPORT";
        case SymbolVisibility.UNRECOGNIZED:
        default:
          return "UNRECOGNIZED";
      }
    }
    exports2.symbolVisibilityToJSON = symbolVisibilityToJSON;
    var ExtensionRangeOptions_VerificationState;
    (function(ExtensionRangeOptions_VerificationState2) {
      ExtensionRangeOptions_VerificationState2[ExtensionRangeOptions_VerificationState2["DECLARATION"] = 0] = "DECLARATION";
      ExtensionRangeOptions_VerificationState2[ExtensionRangeOptions_VerificationState2["UNVERIFIED"] = 1] = "UNVERIFIED";
      ExtensionRangeOptions_VerificationState2[ExtensionRangeOptions_VerificationState2["UNRECOGNIZED"] = -1] = "UNRECOGNIZED";
    })(ExtensionRangeOptions_VerificationState || (exports2.ExtensionRangeOptions_VerificationState = ExtensionRangeOptions_VerificationState = {}));
    function extensionRangeOptions_VerificationStateFromJSON(object) {
      switch (object) {
        case 0:
        case "DECLARATION":
          return ExtensionRangeOptions_VerificationState.DECLARATION;
        case 1:
        case "UNVERIFIED":
          return ExtensionRangeOptions_VerificationState.UNVERIFIED;
        case -1:
        case "UNRECOGNIZED":
        default:
          return ExtensionRangeOptions_VerificationState.UNRECOGNIZED;
      }
    }
    exports2.extensionRangeOptions_VerificationStateFromJSON = extensionRangeOptions_VerificationStateFromJSON;
    function extensionRangeOptions_VerificationStateToJSON(object) {
      switch (object) {
        case ExtensionRangeOptions_VerificationState.DECLARATION:
          return "DECLARATION";
        case ExtensionRangeOptions_VerificationState.UNVERIFIED:
          return "UNVERIFIED";
        case ExtensionRangeOptions_VerificationState.UNRECOGNIZED:
        default:
          return "UNRECOGNIZED";
      }
    }
    exports2.extensionRangeOptions_VerificationStateToJSON = extensionRangeOptions_VerificationStateToJSON;
    var FieldDescriptorProto_Type;
    (function(FieldDescriptorProto_Type2) {
      FieldDescriptorProto_Type2[FieldDescriptorProto_Type2["TYPE_DOUBLE"] = 1] = "TYPE_DOUBLE";
      FieldDescriptorProto_Type2[FieldDescriptorProto_Type2["TYPE_FLOAT"] = 2] = "TYPE_FLOAT";
      FieldDescriptorProto_Type2[FieldDescriptorProto_Type2["TYPE_INT64"] = 3] = "TYPE_INT64";
      FieldDescriptorProto_Type2[FieldDescriptorProto_Type2["TYPE_UINT64"] = 4] = "TYPE_UINT64";
      FieldDescriptorProto_Type2[FieldDescriptorProto_Type2["TYPE_INT32"] = 5] = "TYPE_INT32";
      FieldDescriptorProto_Type2[FieldDescriptorProto_Type2["TYPE_FIXED64"] = 6] = "TYPE_FIXED64";
      FieldDescriptorProto_Type2[FieldDescriptorProto_Type2["TYPE_FIXED32"] = 7] = "TYPE_FIXED32";
      FieldDescriptorProto_Type2[FieldDescriptorProto_Type2["TYPE_BOOL"] = 8] = "TYPE_BOOL";
      FieldDescriptorProto_Type2[FieldDescriptorProto_Type2["TYPE_STRING"] = 9] = "TYPE_STRING";
      FieldDescriptorProto_Type2[FieldDescriptorProto_Type2["TYPE_GROUP"] = 10] = "TYPE_GROUP";
      FieldDescriptorProto_Type2[FieldDescriptorProto_Type2["TYPE_MESSAGE"] = 11] = "TYPE_MESSAGE";
      FieldDescriptorProto_Type2[FieldDescriptorProto_Type2["TYPE_BYTES"] = 12] = "TYPE_BYTES";
      FieldDescriptorProto_Type2[FieldDescriptorProto_Type2["TYPE_UINT32"] = 13] = "TYPE_UINT32";
      FieldDescriptorProto_Type2[FieldDescriptorProto_Type2["TYPE_ENUM"] = 14] = "TYPE_ENUM";
      FieldDescriptorProto_Type2[FieldDescriptorProto_Type2["TYPE_SFIXED32"] = 15] = "TYPE_SFIXED32";
      FieldDescriptorProto_Type2[FieldDescriptorProto_Type2["TYPE_SFIXED64"] = 16] = "TYPE_SFIXED64";
      FieldDescriptorProto_Type2[FieldDescriptorProto_Type2["TYPE_SINT32"] = 17] = "TYPE_SINT32";
      FieldDescriptorProto_Type2[FieldDescriptorProto_Type2["TYPE_SINT64"] = 18] = "TYPE_SINT64";
      FieldDescriptorProto_Type2[FieldDescriptorProto_Type2["UNRECOGNIZED"] = -1] = "UNRECOGNIZED";
    })(FieldDescriptorProto_Type || (exports2.FieldDescriptorProto_Type = FieldDescriptorProto_Type = {}));
    function fieldDescriptorProto_TypeFromJSON(object) {
      switch (object) {
        case 1:
        case "TYPE_DOUBLE":
          return FieldDescriptorProto_Type.TYPE_DOUBLE;
        case 2:
        case "TYPE_FLOAT":
          return FieldDescriptorProto_Type.TYPE_FLOAT;
        case 3:
        case "TYPE_INT64":
          return FieldDescriptorProto_Type.TYPE_INT64;
        case 4:
        case "TYPE_UINT64":
          return FieldDescriptorProto_Type.TYPE_UINT64;
        case 5:
        case "TYPE_INT32":
          return FieldDescriptorProto_Type.TYPE_INT32;
        case 6:
        case "TYPE_FIXED64":
          return FieldDescriptorProto_Type.TYPE_FIXED64;
        case 7:
        case "TYPE_FIXED32":
          return FieldDescriptorProto_Type.TYPE_FIXED32;
        case 8:
        case "TYPE_BOOL":
          return FieldDescriptorProto_Type.TYPE_BOOL;
        case 9:
        case "TYPE_STRING":
          return FieldDescriptorProto_Type.TYPE_STRING;
        case 10:
        case "TYPE_GROUP":
          return FieldDescriptorProto_Type.TYPE_GROUP;
        case 11:
        case "TYPE_MESSAGE":
          return FieldDescriptorProto_Type.TYPE_MESSAGE;
        case 12:
        case "TYPE_BYTES":
          return FieldDescriptorProto_Type.TYPE_BYTES;
        case 13:
        case "TYPE_UINT32":
          return FieldDescriptorProto_Type.TYPE_UINT32;
        case 14:
        case "TYPE_ENUM":
          return FieldDescriptorProto_Type.TYPE_ENUM;
        case 15:
        case "TYPE_SFIXED32":
          return FieldDescriptorProto_Type.TYPE_SFIXED32;
        case 16:
        case "TYPE_SFIXED64":
          return FieldDescriptorProto_Type.TYPE_SFIXED64;
        case 17:
        case "TYPE_SINT32":
          return FieldDescriptorProto_Type.TYPE_SINT32;
        case 18:
        case "TYPE_SINT64":
          return FieldDescriptorProto_Type.TYPE_SINT64;
        case -1:
        case "UNRECOGNIZED":
        default:
          return FieldDescriptorProto_Type.UNRECOGNIZED;
      }
    }
    exports2.fieldDescriptorProto_TypeFromJSON = fieldDescriptorProto_TypeFromJSON;
    function fieldDescriptorProto_TypeToJSON(object) {
      switch (object) {
        case FieldDescriptorProto_Type.TYPE_DOUBLE:
          return "TYPE_DOUBLE";
        case FieldDescriptorProto_Type.TYPE_FLOAT:
          return "TYPE_FLOAT";
        case FieldDescriptorProto_Type.TYPE_INT64:
          return "TYPE_INT64";
        case FieldDescriptorProto_Type.TYPE_UINT64:
          return "TYPE_UINT64";
        case FieldDescriptorProto_Type.TYPE_INT32:
          return "TYPE_INT32";
        case FieldDescriptorProto_Type.TYPE_FIXED64:
          return "TYPE_FIXED64";
        case FieldDescriptorProto_Type.TYPE_FIXED32:
          return "TYPE_FIXED32";
        case FieldDescriptorProto_Type.TYPE_BOOL:
          return "TYPE_BOOL";
        case FieldDescriptorProto_Type.TYPE_STRING:
          return "TYPE_STRING";
        case FieldDescriptorProto_Type.TYPE_GROUP:
          return "TYPE_GROUP";
        case FieldDescriptorProto_Type.TYPE_MESSAGE:
          return "TYPE_MESSAGE";
        case FieldDescriptorProto_Type.TYPE_BYTES:
          return "TYPE_BYTES";
        case FieldDescriptorProto_Type.TYPE_UINT32:
          return "TYPE_UINT32";
        case FieldDescriptorProto_Type.TYPE_ENUM:
          return "TYPE_ENUM";
        case FieldDescriptorProto_Type.TYPE_SFIXED32:
          return "TYPE_SFIXED32";
        case FieldDescriptorProto_Type.TYPE_SFIXED64:
          return "TYPE_SFIXED64";
        case FieldDescriptorProto_Type.TYPE_SINT32:
          return "TYPE_SINT32";
        case FieldDescriptorProto_Type.TYPE_SINT64:
          return "TYPE_SINT64";
        case FieldDescriptorProto_Type.UNRECOGNIZED:
        default:
          return "UNRECOGNIZED";
      }
    }
    exports2.fieldDescriptorProto_TypeToJSON = fieldDescriptorProto_TypeToJSON;
    var FieldDescriptorProto_Label;
    (function(FieldDescriptorProto_Label2) {
      FieldDescriptorProto_Label2[FieldDescriptorProto_Label2["LABEL_OPTIONAL"] = 1] = "LABEL_OPTIONAL";
      FieldDescriptorProto_Label2[FieldDescriptorProto_Label2["LABEL_REPEATED"] = 3] = "LABEL_REPEATED";
      FieldDescriptorProto_Label2[FieldDescriptorProto_Label2["LABEL_REQUIRED"] = 2] = "LABEL_REQUIRED";
      FieldDescriptorProto_Label2[FieldDescriptorProto_Label2["UNRECOGNIZED"] = -1] = "UNRECOGNIZED";
    })(FieldDescriptorProto_Label || (exports2.FieldDescriptorProto_Label = FieldDescriptorProto_Label = {}));
    function fieldDescriptorProto_LabelFromJSON(object) {
      switch (object) {
        case 1:
        case "LABEL_OPTIONAL":
          return FieldDescriptorProto_Label.LABEL_OPTIONAL;
        case 3:
        case "LABEL_REPEATED":
          return FieldDescriptorProto_Label.LABEL_REPEATED;
        case 2:
        case "LABEL_REQUIRED":
          return FieldDescriptorProto_Label.LABEL_REQUIRED;
        case -1:
        case "UNRECOGNIZED":
        default:
          return FieldDescriptorProto_Label.UNRECOGNIZED;
      }
    }
    exports2.fieldDescriptorProto_LabelFromJSON = fieldDescriptorProto_LabelFromJSON;
    function fieldDescriptorProto_LabelToJSON(object) {
      switch (object) {
        case FieldDescriptorProto_Label.LABEL_OPTIONAL:
          return "LABEL_OPTIONAL";
        case FieldDescriptorProto_Label.LABEL_REPEATED:
          return "LABEL_REPEATED";
        case FieldDescriptorProto_Label.LABEL_REQUIRED:
          return "LABEL_REQUIRED";
        case FieldDescriptorProto_Label.UNRECOGNIZED:
        default:
          return "UNRECOGNIZED";
      }
    }
    exports2.fieldDescriptorProto_LabelToJSON = fieldDescriptorProto_LabelToJSON;
    var FileOptions_OptimizeMode;
    (function(FileOptions_OptimizeMode2) {
      FileOptions_OptimizeMode2[FileOptions_OptimizeMode2["SPEED"] = 1] = "SPEED";
      FileOptions_OptimizeMode2[FileOptions_OptimizeMode2["CODE_SIZE"] = 2] = "CODE_SIZE";
      FileOptions_OptimizeMode2[FileOptions_OptimizeMode2["LITE_RUNTIME"] = 3] = "LITE_RUNTIME";
      FileOptions_OptimizeMode2[FileOptions_OptimizeMode2["UNRECOGNIZED"] = -1] = "UNRECOGNIZED";
    })(FileOptions_OptimizeMode || (exports2.FileOptions_OptimizeMode = FileOptions_OptimizeMode = {}));
    function fileOptions_OptimizeModeFromJSON(object) {
      switch (object) {
        case 1:
        case "SPEED":
          return FileOptions_OptimizeMode.SPEED;
        case 2:
        case "CODE_SIZE":
          return FileOptions_OptimizeMode.CODE_SIZE;
        case 3:
        case "LITE_RUNTIME":
          return FileOptions_OptimizeMode.LITE_RUNTIME;
        case -1:
        case "UNRECOGNIZED":
        default:
          return FileOptions_OptimizeMode.UNRECOGNIZED;
      }
    }
    exports2.fileOptions_OptimizeModeFromJSON = fileOptions_OptimizeModeFromJSON;
    function fileOptions_OptimizeModeToJSON(object) {
      switch (object) {
        case FileOptions_OptimizeMode.SPEED:
          return "SPEED";
        case FileOptions_OptimizeMode.CODE_SIZE:
          return "CODE_SIZE";
        case FileOptions_OptimizeMode.LITE_RUNTIME:
          return "LITE_RUNTIME";
        case FileOptions_OptimizeMode.UNRECOGNIZED:
        default:
          return "UNRECOGNIZED";
      }
    }
    exports2.fileOptions_OptimizeModeToJSON = fileOptions_OptimizeModeToJSON;
    var FieldOptions_CType;
    (function(FieldOptions_CType2) {
      FieldOptions_CType2[FieldOptions_CType2["STRING"] = 0] = "STRING";
      FieldOptions_CType2[FieldOptions_CType2["CORD"] = 1] = "CORD";
      FieldOptions_CType2[FieldOptions_CType2["STRING_PIECE"] = 2] = "STRING_PIECE";
      FieldOptions_CType2[FieldOptions_CType2["UNRECOGNIZED"] = -1] = "UNRECOGNIZED";
    })(FieldOptions_CType || (exports2.FieldOptions_CType = FieldOptions_CType = {}));
    function fieldOptions_CTypeFromJSON(object) {
      switch (object) {
        case 0:
        case "STRING":
          return FieldOptions_CType.STRING;
        case 1:
        case "CORD":
          return FieldOptions_CType.CORD;
        case 2:
        case "STRING_PIECE":
          return FieldOptions_CType.STRING_PIECE;
        case -1:
        case "UNRECOGNIZED":
        default:
          return FieldOptions_CType.UNRECOGNIZED;
      }
    }
    exports2.fieldOptions_CTypeFromJSON = fieldOptions_CTypeFromJSON;
    function fieldOptions_CTypeToJSON(object) {
      switch (object) {
        case FieldOptions_CType.STRING:
          return "STRING";
        case FieldOptions_CType.CORD:
          return "CORD";
        case FieldOptions_CType.STRING_PIECE:
          return "STRING_PIECE";
        case FieldOptions_CType.UNRECOGNIZED:
        default:
          return "UNRECOGNIZED";
      }
    }
    exports2.fieldOptions_CTypeToJSON = fieldOptions_CTypeToJSON;
    var FieldOptions_JSType;
    (function(FieldOptions_JSType2) {
      FieldOptions_JSType2[FieldOptions_JSType2["JS_NORMAL"] = 0] = "JS_NORMAL";
      FieldOptions_JSType2[FieldOptions_JSType2["JS_STRING"] = 1] = "JS_STRING";
      FieldOptions_JSType2[FieldOptions_JSType2["JS_NUMBER"] = 2] = "JS_NUMBER";
      FieldOptions_JSType2[FieldOptions_JSType2["UNRECOGNIZED"] = -1] = "UNRECOGNIZED";
    })(FieldOptions_JSType || (exports2.FieldOptions_JSType = FieldOptions_JSType = {}));
    function fieldOptions_JSTypeFromJSON(object) {
      switch (object) {
        case 0:
        case "JS_NORMAL":
          return FieldOptions_JSType.JS_NORMAL;
        case 1:
        case "JS_STRING":
          return FieldOptions_JSType.JS_STRING;
        case 2:
        case "JS_NUMBER":
          return FieldOptions_JSType.JS_NUMBER;
        case -1:
        case "UNRECOGNIZED":
        default:
          return FieldOptions_JSType.UNRECOGNIZED;
      }
    }
    exports2.fieldOptions_JSTypeFromJSON = fieldOptions_JSTypeFromJSON;
    function fieldOptions_JSTypeToJSON(object) {
      switch (object) {
        case FieldOptions_JSType.JS_NORMAL:
          return "JS_NORMAL";
        case FieldOptions_JSType.JS_STRING:
          return "JS_STRING";
        case FieldOptions_JSType.JS_NUMBER:
          return "JS_NUMBER";
        case FieldOptions_JSType.UNRECOGNIZED:
        default:
          return "UNRECOGNIZED";
      }
    }
    exports2.fieldOptions_JSTypeToJSON = fieldOptions_JSTypeToJSON;
    var FieldOptions_OptionRetention;
    (function(FieldOptions_OptionRetention2) {
      FieldOptions_OptionRetention2[FieldOptions_OptionRetention2["RETENTION_UNKNOWN"] = 0] = "RETENTION_UNKNOWN";
      FieldOptions_OptionRetention2[FieldOptions_OptionRetention2["RETENTION_RUNTIME"] = 1] = "RETENTION_RUNTIME";
      FieldOptions_OptionRetention2[FieldOptions_OptionRetention2["RETENTION_SOURCE"] = 2] = "RETENTION_SOURCE";
      FieldOptions_OptionRetention2[FieldOptions_OptionRetention2["UNRECOGNIZED"] = -1] = "UNRECOGNIZED";
    })(FieldOptions_OptionRetention || (exports2.FieldOptions_OptionRetention = FieldOptions_OptionRetention = {}));
    function fieldOptions_OptionRetentionFromJSON(object) {
      switch (object) {
        case 0:
        case "RETENTION_UNKNOWN":
          return FieldOptions_OptionRetention.RETENTION_UNKNOWN;
        case 1:
        case "RETENTION_RUNTIME":
          return FieldOptions_OptionRetention.RETENTION_RUNTIME;
        case 2:
        case "RETENTION_SOURCE":
          return FieldOptions_OptionRetention.RETENTION_SOURCE;
        case -1:
        case "UNRECOGNIZED":
        default:
          return FieldOptions_OptionRetention.UNRECOGNIZED;
      }
    }
    exports2.fieldOptions_OptionRetentionFromJSON = fieldOptions_OptionRetentionFromJSON;
    function fieldOptions_OptionRetentionToJSON(object) {
      switch (object) {
        case FieldOptions_OptionRetention.RETENTION_UNKNOWN:
          return "RETENTION_UNKNOWN";
        case FieldOptions_OptionRetention.RETENTION_RUNTIME:
          return "RETENTION_RUNTIME";
        case FieldOptions_OptionRetention.RETENTION_SOURCE:
          return "RETENTION_SOURCE";
        case FieldOptions_OptionRetention.UNRECOGNIZED:
        default:
          return "UNRECOGNIZED";
      }
    }
    exports2.fieldOptions_OptionRetentionToJSON = fieldOptions_OptionRetentionToJSON;
    var FieldOptions_OptionTargetType;
    (function(FieldOptions_OptionTargetType2) {
      FieldOptions_OptionTargetType2[FieldOptions_OptionTargetType2["TARGET_TYPE_UNKNOWN"] = 0] = "TARGET_TYPE_UNKNOWN";
      FieldOptions_OptionTargetType2[FieldOptions_OptionTargetType2["TARGET_TYPE_FILE"] = 1] = "TARGET_TYPE_FILE";
      FieldOptions_OptionTargetType2[FieldOptions_OptionTargetType2["TARGET_TYPE_EXTENSION_RANGE"] = 2] = "TARGET_TYPE_EXTENSION_RANGE";
      FieldOptions_OptionTargetType2[FieldOptions_OptionTargetType2["TARGET_TYPE_MESSAGE"] = 3] = "TARGET_TYPE_MESSAGE";
      FieldOptions_OptionTargetType2[FieldOptions_OptionTargetType2["TARGET_TYPE_FIELD"] = 4] = "TARGET_TYPE_FIELD";
      FieldOptions_OptionTargetType2[FieldOptions_OptionTargetType2["TARGET_TYPE_ONEOF"] = 5] = "TARGET_TYPE_ONEOF";
      FieldOptions_OptionTargetType2[FieldOptions_OptionTargetType2["TARGET_TYPE_ENUM"] = 6] = "TARGET_TYPE_ENUM";
      FieldOptions_OptionTargetType2[FieldOptions_OptionTargetType2["TARGET_TYPE_ENUM_ENTRY"] = 7] = "TARGET_TYPE_ENUM_ENTRY";
      FieldOptions_OptionTargetType2[FieldOptions_OptionTargetType2["TARGET_TYPE_SERVICE"] = 8] = "TARGET_TYPE_SERVICE";
      FieldOptions_OptionTargetType2[FieldOptions_OptionTargetType2["TARGET_TYPE_METHOD"] = 9] = "TARGET_TYPE_METHOD";
      FieldOptions_OptionTargetType2[FieldOptions_OptionTargetType2["UNRECOGNIZED"] = -1] = "UNRECOGNIZED";
    })(FieldOptions_OptionTargetType || (exports2.FieldOptions_OptionTargetType = FieldOptions_OptionTargetType = {}));
    function fieldOptions_OptionTargetTypeFromJSON(object) {
      switch (object) {
        case 0:
        case "TARGET_TYPE_UNKNOWN":
          return FieldOptions_OptionTargetType.TARGET_TYPE_UNKNOWN;
        case 1:
        case "TARGET_TYPE_FILE":
          return FieldOptions_OptionTargetType.TARGET_TYPE_FILE;
        case 2:
        case "TARGET_TYPE_EXTENSION_RANGE":
          return FieldOptions_OptionTargetType.TARGET_TYPE_EXTENSION_RANGE;
        case 3:
        case "TARGET_TYPE_MESSAGE":
          return FieldOptions_OptionTargetType.TARGET_TYPE_MESSAGE;
        case 4:
        case "TARGET_TYPE_FIELD":
          return FieldOptions_OptionTargetType.TARGET_TYPE_FIELD;
        case 5:
        case "TARGET_TYPE_ONEOF":
          return FieldOptions_OptionTargetType.TARGET_TYPE_ONEOF;
        case 6:
        case "TARGET_TYPE_ENUM":
          return FieldOptions_OptionTargetType.TARGET_TYPE_ENUM;
        case 7:
        case "TARGET_TYPE_ENUM_ENTRY":
          return FieldOptions_OptionTargetType.TARGET_TYPE_ENUM_ENTRY;
        case 8:
        case "TARGET_TYPE_SERVICE":
          return FieldOptions_OptionTargetType.TARGET_TYPE_SERVICE;
        case 9:
        case "TARGET_TYPE_METHOD":
          return FieldOptions_OptionTargetType.TARGET_TYPE_METHOD;
        case -1:
        case "UNRECOGNIZED":
        default:
          return FieldOptions_OptionTargetType.UNRECOGNIZED;
      }
    }
    exports2.fieldOptions_OptionTargetTypeFromJSON = fieldOptions_OptionTargetTypeFromJSON;
    function fieldOptions_OptionTargetTypeToJSON(object) {
      switch (object) {
        case FieldOptions_OptionTargetType.TARGET_TYPE_UNKNOWN:
          return "TARGET_TYPE_UNKNOWN";
        case FieldOptions_OptionTargetType.TARGET_TYPE_FILE:
          return "TARGET_TYPE_FILE";
        case FieldOptions_OptionTargetType.TARGET_TYPE_EXTENSION_RANGE:
          return "TARGET_TYPE_EXTENSION_RANGE";
        case FieldOptions_OptionTargetType.TARGET_TYPE_MESSAGE:
          return "TARGET_TYPE_MESSAGE";
        case FieldOptions_OptionTargetType.TARGET_TYPE_FIELD:
          return "TARGET_TYPE_FIELD";
        case FieldOptions_OptionTargetType.TARGET_TYPE_ONEOF:
          return "TARGET_TYPE_ONEOF";
        case FieldOptions_OptionTargetType.TARGET_TYPE_ENUM:
          return "TARGET_TYPE_ENUM";
        case FieldOptions_OptionTargetType.TARGET_TYPE_ENUM_ENTRY:
          return "TARGET_TYPE_ENUM_ENTRY";
        case FieldOptions_OptionTargetType.TARGET_TYPE_SERVICE:
          return "TARGET_TYPE_SERVICE";
        case FieldOptions_OptionTargetType.TARGET_TYPE_METHOD:
          return "TARGET_TYPE_METHOD";
        case FieldOptions_OptionTargetType.UNRECOGNIZED:
        default:
          return "UNRECOGNIZED";
      }
    }
    exports2.fieldOptions_OptionTargetTypeToJSON = fieldOptions_OptionTargetTypeToJSON;
    var MethodOptions_IdempotencyLevel;
    (function(MethodOptions_IdempotencyLevel2) {
      MethodOptions_IdempotencyLevel2[MethodOptions_IdempotencyLevel2["IDEMPOTENCY_UNKNOWN"] = 0] = "IDEMPOTENCY_UNKNOWN";
      MethodOptions_IdempotencyLevel2[MethodOptions_IdempotencyLevel2["NO_SIDE_EFFECTS"] = 1] = "NO_SIDE_EFFECTS";
      MethodOptions_IdempotencyLevel2[MethodOptions_IdempotencyLevel2["IDEMPOTENT"] = 2] = "IDEMPOTENT";
      MethodOptions_IdempotencyLevel2[MethodOptions_IdempotencyLevel2["UNRECOGNIZED"] = -1] = "UNRECOGNIZED";
    })(MethodOptions_IdempotencyLevel || (exports2.MethodOptions_IdempotencyLevel = MethodOptions_IdempotencyLevel = {}));
    function methodOptions_IdempotencyLevelFromJSON(object) {
      switch (object) {
        case 0:
        case "IDEMPOTENCY_UNKNOWN":
          return MethodOptions_IdempotencyLevel.IDEMPOTENCY_UNKNOWN;
        case 1:
        case "NO_SIDE_EFFECTS":
          return MethodOptions_IdempotencyLevel.NO_SIDE_EFFECTS;
        case 2:
        case "IDEMPOTENT":
          return MethodOptions_IdempotencyLevel.IDEMPOTENT;
        case -1:
        case "UNRECOGNIZED":
        default:
          return MethodOptions_IdempotencyLevel.UNRECOGNIZED;
      }
    }
    exports2.methodOptions_IdempotencyLevelFromJSON = methodOptions_IdempotencyLevelFromJSON;
    function methodOptions_IdempotencyLevelToJSON(object) {
      switch (object) {
        case MethodOptions_IdempotencyLevel.IDEMPOTENCY_UNKNOWN:
          return "IDEMPOTENCY_UNKNOWN";
        case MethodOptions_IdempotencyLevel.NO_SIDE_EFFECTS:
          return "NO_SIDE_EFFECTS";
        case MethodOptions_IdempotencyLevel.IDEMPOTENT:
          return "IDEMPOTENT";
        case MethodOptions_IdempotencyLevel.UNRECOGNIZED:
        default:
          return "UNRECOGNIZED";
      }
    }
    exports2.methodOptions_IdempotencyLevelToJSON = methodOptions_IdempotencyLevelToJSON;
    var FeatureSet_FieldPresence;
    (function(FeatureSet_FieldPresence2) {
      FeatureSet_FieldPresence2[FeatureSet_FieldPresence2["FIELD_PRESENCE_UNKNOWN"] = 0] = "FIELD_PRESENCE_UNKNOWN";
      FeatureSet_FieldPresence2[FeatureSet_FieldPresence2["EXPLICIT"] = 1] = "EXPLICIT";
      FeatureSet_FieldPresence2[FeatureSet_FieldPresence2["IMPLICIT"] = 2] = "IMPLICIT";
      FeatureSet_FieldPresence2[FeatureSet_FieldPresence2["LEGACY_REQUIRED"] = 3] = "LEGACY_REQUIRED";
      FeatureSet_FieldPresence2[FeatureSet_FieldPresence2["UNRECOGNIZED"] = -1] = "UNRECOGNIZED";
    })(FeatureSet_FieldPresence || (exports2.FeatureSet_FieldPresence = FeatureSet_FieldPresence = {}));
    function featureSet_FieldPresenceFromJSON(object) {
      switch (object) {
        case 0:
        case "FIELD_PRESENCE_UNKNOWN":
          return FeatureSet_FieldPresence.FIELD_PRESENCE_UNKNOWN;
        case 1:
        case "EXPLICIT":
          return FeatureSet_FieldPresence.EXPLICIT;
        case 2:
        case "IMPLICIT":
          return FeatureSet_FieldPresence.IMPLICIT;
        case 3:
        case "LEGACY_REQUIRED":
          return FeatureSet_FieldPresence.LEGACY_REQUIRED;
        case -1:
        case "UNRECOGNIZED":
        default:
          return FeatureSet_FieldPresence.UNRECOGNIZED;
      }
    }
    exports2.featureSet_FieldPresenceFromJSON = featureSet_FieldPresenceFromJSON;
    function featureSet_FieldPresenceToJSON(object) {
      switch (object) {
        case FeatureSet_FieldPresence.FIELD_PRESENCE_UNKNOWN:
          return "FIELD_PRESENCE_UNKNOWN";
        case FeatureSet_FieldPresence.EXPLICIT:
          return "EXPLICIT";
        case FeatureSet_FieldPresence.IMPLICIT:
          return "IMPLICIT";
        case FeatureSet_FieldPresence.LEGACY_REQUIRED:
          return "LEGACY_REQUIRED";
        case FeatureSet_FieldPresence.UNRECOGNIZED:
        default:
          return "UNRECOGNIZED";
      }
    }
    exports2.featureSet_FieldPresenceToJSON = featureSet_FieldPresenceToJSON;
    var FeatureSet_EnumType;
    (function(FeatureSet_EnumType2) {
      FeatureSet_EnumType2[FeatureSet_EnumType2["ENUM_TYPE_UNKNOWN"] = 0] = "ENUM_TYPE_UNKNOWN";
      FeatureSet_EnumType2[FeatureSet_EnumType2["OPEN"] = 1] = "OPEN";
      FeatureSet_EnumType2[FeatureSet_EnumType2["CLOSED"] = 2] = "CLOSED";
      FeatureSet_EnumType2[FeatureSet_EnumType2["UNRECOGNIZED"] = -1] = "UNRECOGNIZED";
    })(FeatureSet_EnumType || (exports2.FeatureSet_EnumType = FeatureSet_EnumType = {}));
    function featureSet_EnumTypeFromJSON(object) {
      switch (object) {
        case 0:
        case "ENUM_TYPE_UNKNOWN":
          return FeatureSet_EnumType.ENUM_TYPE_UNKNOWN;
        case 1:
        case "OPEN":
          return FeatureSet_EnumType.OPEN;
        case 2:
        case "CLOSED":
          return FeatureSet_EnumType.CLOSED;
        case -1:
        case "UNRECOGNIZED":
        default:
          return FeatureSet_EnumType.UNRECOGNIZED;
      }
    }
    exports2.featureSet_EnumTypeFromJSON = featureSet_EnumTypeFromJSON;
    function featureSet_EnumTypeToJSON(object) {
      switch (object) {
        case FeatureSet_EnumType.ENUM_TYPE_UNKNOWN:
          return "ENUM_TYPE_UNKNOWN";
        case FeatureSet_EnumType.OPEN:
          return "OPEN";
        case FeatureSet_EnumType.CLOSED:
          return "CLOSED";
        case FeatureSet_EnumType.UNRECOGNIZED:
        default:
          return "UNRECOGNIZED";
      }
    }
    exports2.featureSet_EnumTypeToJSON = featureSet_EnumTypeToJSON;
    var FeatureSet_RepeatedFieldEncoding;
    (function(FeatureSet_RepeatedFieldEncoding2) {
      FeatureSet_RepeatedFieldEncoding2[FeatureSet_RepeatedFieldEncoding2["REPEATED_FIELD_ENCODING_UNKNOWN"] = 0] = "REPEATED_FIELD_ENCODING_UNKNOWN";
      FeatureSet_RepeatedFieldEncoding2[FeatureSet_RepeatedFieldEncoding2["PACKED"] = 1] = "PACKED";
      FeatureSet_RepeatedFieldEncoding2[FeatureSet_RepeatedFieldEncoding2["EXPANDED"] = 2] = "EXPANDED";
      FeatureSet_RepeatedFieldEncoding2[FeatureSet_RepeatedFieldEncoding2["UNRECOGNIZED"] = -1] = "UNRECOGNIZED";
    })(FeatureSet_RepeatedFieldEncoding || (exports2.FeatureSet_RepeatedFieldEncoding = FeatureSet_RepeatedFieldEncoding = {}));
    function featureSet_RepeatedFieldEncodingFromJSON(object) {
      switch (object) {
        case 0:
        case "REPEATED_FIELD_ENCODING_UNKNOWN":
          return FeatureSet_RepeatedFieldEncoding.REPEATED_FIELD_ENCODING_UNKNOWN;
        case 1:
        case "PACKED":
          return FeatureSet_RepeatedFieldEncoding.PACKED;
        case 2:
        case "EXPANDED":
          return FeatureSet_RepeatedFieldEncoding.EXPANDED;
        case -1:
        case "UNRECOGNIZED":
        default:
          return FeatureSet_RepeatedFieldEncoding.UNRECOGNIZED;
      }
    }
    exports2.featureSet_RepeatedFieldEncodingFromJSON = featureSet_RepeatedFieldEncodingFromJSON;
    function featureSet_RepeatedFieldEncodingToJSON(object) {
      switch (object) {
        case FeatureSet_RepeatedFieldEncoding.REPEATED_FIELD_ENCODING_UNKNOWN:
          return "REPEATED_FIELD_ENCODING_UNKNOWN";
        case FeatureSet_RepeatedFieldEncoding.PACKED:
          return "PACKED";
        case FeatureSet_RepeatedFieldEncoding.EXPANDED:
          return "EXPANDED";
        case FeatureSet_RepeatedFieldEncoding.UNRECOGNIZED:
        default:
          return "UNRECOGNIZED";
      }
    }
    exports2.featureSet_RepeatedFieldEncodingToJSON = featureSet_RepeatedFieldEncodingToJSON;
    var FeatureSet_Utf8Validation;
    (function(FeatureSet_Utf8Validation2) {
      FeatureSet_Utf8Validation2[FeatureSet_Utf8Validation2["UTF8_VALIDATION_UNKNOWN"] = 0] = "UTF8_VALIDATION_UNKNOWN";
      FeatureSet_Utf8Validation2[FeatureSet_Utf8Validation2["VERIFY"] = 2] = "VERIFY";
      FeatureSet_Utf8Validation2[FeatureSet_Utf8Validation2["NONE"] = 3] = "NONE";
      FeatureSet_Utf8Validation2[FeatureSet_Utf8Validation2["UNRECOGNIZED"] = -1] = "UNRECOGNIZED";
    })(FeatureSet_Utf8Validation || (exports2.FeatureSet_Utf8Validation = FeatureSet_Utf8Validation = {}));
    function featureSet_Utf8ValidationFromJSON(object) {
      switch (object) {
        case 0:
        case "UTF8_VALIDATION_UNKNOWN":
          return FeatureSet_Utf8Validation.UTF8_VALIDATION_UNKNOWN;
        case 2:
        case "VERIFY":
          return FeatureSet_Utf8Validation.VERIFY;
        case 3:
        case "NONE":
          return FeatureSet_Utf8Validation.NONE;
        case -1:
        case "UNRECOGNIZED":
        default:
          return FeatureSet_Utf8Validation.UNRECOGNIZED;
      }
    }
    exports2.featureSet_Utf8ValidationFromJSON = featureSet_Utf8ValidationFromJSON;
    function featureSet_Utf8ValidationToJSON(object) {
      switch (object) {
        case FeatureSet_Utf8Validation.UTF8_VALIDATION_UNKNOWN:
          return "UTF8_VALIDATION_UNKNOWN";
        case FeatureSet_Utf8Validation.VERIFY:
          return "VERIFY";
        case FeatureSet_Utf8Validation.NONE:
          return "NONE";
        case FeatureSet_Utf8Validation.UNRECOGNIZED:
        default:
          return "UNRECOGNIZED";
      }
    }
    exports2.featureSet_Utf8ValidationToJSON = featureSet_Utf8ValidationToJSON;
    var FeatureSet_MessageEncoding;
    (function(FeatureSet_MessageEncoding2) {
      FeatureSet_MessageEncoding2[FeatureSet_MessageEncoding2["MESSAGE_ENCODING_UNKNOWN"] = 0] = "MESSAGE_ENCODING_UNKNOWN";
      FeatureSet_MessageEncoding2[FeatureSet_MessageEncoding2["LENGTH_PREFIXED"] = 1] = "LENGTH_PREFIXED";
      FeatureSet_MessageEncoding2[FeatureSet_MessageEncoding2["DELIMITED"] = 2] = "DELIMITED";
      FeatureSet_MessageEncoding2[FeatureSet_MessageEncoding2["UNRECOGNIZED"] = -1] = "UNRECOGNIZED";
    })(FeatureSet_MessageEncoding || (exports2.FeatureSet_MessageEncoding = FeatureSet_MessageEncoding = {}));
    function featureSet_MessageEncodingFromJSON(object) {
      switch (object) {
        case 0:
        case "MESSAGE_ENCODING_UNKNOWN":
          return FeatureSet_MessageEncoding.MESSAGE_ENCODING_UNKNOWN;
        case 1:
        case "LENGTH_PREFIXED":
          return FeatureSet_MessageEncoding.LENGTH_PREFIXED;
        case 2:
        case "DELIMITED":
          return FeatureSet_MessageEncoding.DELIMITED;
        case -1:
        case "UNRECOGNIZED":
        default:
          return FeatureSet_MessageEncoding.UNRECOGNIZED;
      }
    }
    exports2.featureSet_MessageEncodingFromJSON = featureSet_MessageEncodingFromJSON;
    function featureSet_MessageEncodingToJSON(object) {
      switch (object) {
        case FeatureSet_MessageEncoding.MESSAGE_ENCODING_UNKNOWN:
          return "MESSAGE_ENCODING_UNKNOWN";
        case FeatureSet_MessageEncoding.LENGTH_PREFIXED:
          return "LENGTH_PREFIXED";
        case FeatureSet_MessageEncoding.DELIMITED:
          return "DELIMITED";
        case FeatureSet_MessageEncoding.UNRECOGNIZED:
        default:
          return "UNRECOGNIZED";
      }
    }
    exports2.featureSet_MessageEncodingToJSON = featureSet_MessageEncodingToJSON;
    var FeatureSet_JsonFormat;
    (function(FeatureSet_JsonFormat2) {
      FeatureSet_JsonFormat2[FeatureSet_JsonFormat2["JSON_FORMAT_UNKNOWN"] = 0] = "JSON_FORMAT_UNKNOWN";
      FeatureSet_JsonFormat2[FeatureSet_JsonFormat2["ALLOW"] = 1] = "ALLOW";
      FeatureSet_JsonFormat2[FeatureSet_JsonFormat2["LEGACY_BEST_EFFORT"] = 2] = "LEGACY_BEST_EFFORT";
      FeatureSet_JsonFormat2[FeatureSet_JsonFormat2["UNRECOGNIZED"] = -1] = "UNRECOGNIZED";
    })(FeatureSet_JsonFormat || (exports2.FeatureSet_JsonFormat = FeatureSet_JsonFormat = {}));
    function featureSet_JsonFormatFromJSON(object) {
      switch (object) {
        case 0:
        case "JSON_FORMAT_UNKNOWN":
          return FeatureSet_JsonFormat.JSON_FORMAT_UNKNOWN;
        case 1:
        case "ALLOW":
          return FeatureSet_JsonFormat.ALLOW;
        case 2:
        case "LEGACY_BEST_EFFORT":
          return FeatureSet_JsonFormat.LEGACY_BEST_EFFORT;
        case -1:
        case "UNRECOGNIZED":
        default:
          return FeatureSet_JsonFormat.UNRECOGNIZED;
      }
    }
    exports2.featureSet_JsonFormatFromJSON = featureSet_JsonFormatFromJSON;
    function featureSet_JsonFormatToJSON(object) {
      switch (object) {
        case FeatureSet_JsonFormat.JSON_FORMAT_UNKNOWN:
          return "JSON_FORMAT_UNKNOWN";
        case FeatureSet_JsonFormat.ALLOW:
          return "ALLOW";
        case FeatureSet_JsonFormat.LEGACY_BEST_EFFORT:
          return "LEGACY_BEST_EFFORT";
        case FeatureSet_JsonFormat.UNRECOGNIZED:
        default:
          return "UNRECOGNIZED";
      }
    }
    exports2.featureSet_JsonFormatToJSON = featureSet_JsonFormatToJSON;
    var FeatureSet_EnforceNamingStyle;
    (function(FeatureSet_EnforceNamingStyle2) {
      FeatureSet_EnforceNamingStyle2[FeatureSet_EnforceNamingStyle2["ENFORCE_NAMING_STYLE_UNKNOWN"] = 0] = "ENFORCE_NAMING_STYLE_UNKNOWN";
      FeatureSet_EnforceNamingStyle2[FeatureSet_EnforceNamingStyle2["STYLE2024"] = 1] = "STYLE2024";
      FeatureSet_EnforceNamingStyle2[FeatureSet_EnforceNamingStyle2["STYLE_LEGACY"] = 2] = "STYLE_LEGACY";
      FeatureSet_EnforceNamingStyle2[FeatureSet_EnforceNamingStyle2["UNRECOGNIZED"] = -1] = "UNRECOGNIZED";
    })(FeatureSet_EnforceNamingStyle || (exports2.FeatureSet_EnforceNamingStyle = FeatureSet_EnforceNamingStyle = {}));
    function featureSet_EnforceNamingStyleFromJSON(object) {
      switch (object) {
        case 0:
        case "ENFORCE_NAMING_STYLE_UNKNOWN":
          return FeatureSet_EnforceNamingStyle.ENFORCE_NAMING_STYLE_UNKNOWN;
        case 1:
        case "STYLE2024":
          return FeatureSet_EnforceNamingStyle.STYLE2024;
        case 2:
        case "STYLE_LEGACY":
          return FeatureSet_EnforceNamingStyle.STYLE_LEGACY;
        case -1:
        case "UNRECOGNIZED":
        default:
          return FeatureSet_EnforceNamingStyle.UNRECOGNIZED;
      }
    }
    exports2.featureSet_EnforceNamingStyleFromJSON = featureSet_EnforceNamingStyleFromJSON;
    function featureSet_EnforceNamingStyleToJSON(object) {
      switch (object) {
        case FeatureSet_EnforceNamingStyle.ENFORCE_NAMING_STYLE_UNKNOWN:
          return "ENFORCE_NAMING_STYLE_UNKNOWN";
        case FeatureSet_EnforceNamingStyle.STYLE2024:
          return "STYLE2024";
        case FeatureSet_EnforceNamingStyle.STYLE_LEGACY:
          return "STYLE_LEGACY";
        case FeatureSet_EnforceNamingStyle.UNRECOGNIZED:
        default:
          return "UNRECOGNIZED";
      }
    }
    exports2.featureSet_EnforceNamingStyleToJSON = featureSet_EnforceNamingStyleToJSON;
    var FeatureSet_VisibilityFeature_DefaultSymbolVisibility;
    (function(FeatureSet_VisibilityFeature_DefaultSymbolVisibility2) {
      FeatureSet_VisibilityFeature_DefaultSymbolVisibility2[FeatureSet_VisibilityFeature_DefaultSymbolVisibility2["DEFAULT_SYMBOL_VISIBILITY_UNKNOWN"] = 0] = "DEFAULT_SYMBOL_VISIBILITY_UNKNOWN";
      FeatureSet_VisibilityFeature_DefaultSymbolVisibility2[FeatureSet_VisibilityFeature_DefaultSymbolVisibility2["EXPORT_ALL"] = 1] = "EXPORT_ALL";
      FeatureSet_VisibilityFeature_DefaultSymbolVisibility2[FeatureSet_VisibilityFeature_DefaultSymbolVisibility2["EXPORT_TOP_LEVEL"] = 2] = "EXPORT_TOP_LEVEL";
      FeatureSet_VisibilityFeature_DefaultSymbolVisibility2[FeatureSet_VisibilityFeature_DefaultSymbolVisibility2["LOCAL_ALL"] = 3] = "LOCAL_ALL";
      FeatureSet_VisibilityFeature_DefaultSymbolVisibility2[FeatureSet_VisibilityFeature_DefaultSymbolVisibility2["STRICT"] = 4] = "STRICT";
      FeatureSet_VisibilityFeature_DefaultSymbolVisibility2[FeatureSet_VisibilityFeature_DefaultSymbolVisibility2["UNRECOGNIZED"] = -1] = "UNRECOGNIZED";
    })(FeatureSet_VisibilityFeature_DefaultSymbolVisibility || (exports2.FeatureSet_VisibilityFeature_DefaultSymbolVisibility = FeatureSet_VisibilityFeature_DefaultSymbolVisibility = {}));
    function featureSet_VisibilityFeature_DefaultSymbolVisibilityFromJSON(object) {
      switch (object) {
        case 0:
        case "DEFAULT_SYMBOL_VISIBILITY_UNKNOWN":
          return FeatureSet_VisibilityFeature_DefaultSymbolVisibility.DEFAULT_SYMBOL_VISIBILITY_UNKNOWN;
        case 1:
        case "EXPORT_ALL":
          return FeatureSet_VisibilityFeature_DefaultSymbolVisibility.EXPORT_ALL;
        case 2:
        case "EXPORT_TOP_LEVEL":
          return FeatureSet_VisibilityFeature_DefaultSymbolVisibility.EXPORT_TOP_LEVEL;
        case 3:
        case "LOCAL_ALL":
          return FeatureSet_VisibilityFeature_DefaultSymbolVisibility.LOCAL_ALL;
        case 4:
        case "STRICT":
          return FeatureSet_VisibilityFeature_DefaultSymbolVisibility.STRICT;
        case -1:
        case "UNRECOGNIZED":
        default:
          return FeatureSet_VisibilityFeature_DefaultSymbolVisibility.UNRECOGNIZED;
      }
    }
    exports2.featureSet_VisibilityFeature_DefaultSymbolVisibilityFromJSON = featureSet_VisibilityFeature_DefaultSymbolVisibilityFromJSON;
    function featureSet_VisibilityFeature_DefaultSymbolVisibilityToJSON(object) {
      switch (object) {
        case FeatureSet_VisibilityFeature_DefaultSymbolVisibility.DEFAULT_SYMBOL_VISIBILITY_UNKNOWN:
          return "DEFAULT_SYMBOL_VISIBILITY_UNKNOWN";
        case FeatureSet_VisibilityFeature_DefaultSymbolVisibility.EXPORT_ALL:
          return "EXPORT_ALL";
        case FeatureSet_VisibilityFeature_DefaultSymbolVisibility.EXPORT_TOP_LEVEL:
          return "EXPORT_TOP_LEVEL";
        case FeatureSet_VisibilityFeature_DefaultSymbolVisibility.LOCAL_ALL:
          return "LOCAL_ALL";
        case FeatureSet_VisibilityFeature_DefaultSymbolVisibility.STRICT:
          return "STRICT";
        case FeatureSet_VisibilityFeature_DefaultSymbolVisibility.UNRECOGNIZED:
        default:
          return "UNRECOGNIZED";
      }
    }
    exports2.featureSet_VisibilityFeature_DefaultSymbolVisibilityToJSON = featureSet_VisibilityFeature_DefaultSymbolVisibilityToJSON;
    var GeneratedCodeInfo_Annotation_Semantic;
    (function(GeneratedCodeInfo_Annotation_Semantic2) {
      GeneratedCodeInfo_Annotation_Semantic2[GeneratedCodeInfo_Annotation_Semantic2["NONE"] = 0] = "NONE";
      GeneratedCodeInfo_Annotation_Semantic2[GeneratedCodeInfo_Annotation_Semantic2["SET"] = 1] = "SET";
      GeneratedCodeInfo_Annotation_Semantic2[GeneratedCodeInfo_Annotation_Semantic2["ALIAS"] = 2] = "ALIAS";
      GeneratedCodeInfo_Annotation_Semantic2[GeneratedCodeInfo_Annotation_Semantic2["UNRECOGNIZED"] = -1] = "UNRECOGNIZED";
    })(GeneratedCodeInfo_Annotation_Semantic || (exports2.GeneratedCodeInfo_Annotation_Semantic = GeneratedCodeInfo_Annotation_Semantic = {}));
    function generatedCodeInfo_Annotation_SemanticFromJSON(object) {
      switch (object) {
        case 0:
        case "NONE":
          return GeneratedCodeInfo_Annotation_Semantic.NONE;
        case 1:
        case "SET":
          return GeneratedCodeInfo_Annotation_Semantic.SET;
        case 2:
        case "ALIAS":
          return GeneratedCodeInfo_Annotation_Semantic.ALIAS;
        case -1:
        case "UNRECOGNIZED":
        default:
          return GeneratedCodeInfo_Annotation_Semantic.UNRECOGNIZED;
      }
    }
    exports2.generatedCodeInfo_Annotation_SemanticFromJSON = generatedCodeInfo_Annotation_SemanticFromJSON;
    function generatedCodeInfo_Annotation_SemanticToJSON(object) {
      switch (object) {
        case GeneratedCodeInfo_Annotation_Semantic.NONE:
          return "NONE";
        case GeneratedCodeInfo_Annotation_Semantic.SET:
          return "SET";
        case GeneratedCodeInfo_Annotation_Semantic.ALIAS:
          return "ALIAS";
        case GeneratedCodeInfo_Annotation_Semantic.UNRECOGNIZED:
        default:
          return "UNRECOGNIZED";
      }
    }
    exports2.generatedCodeInfo_Annotation_SemanticToJSON = generatedCodeInfo_Annotation_SemanticToJSON;
    function createBaseFileDescriptorSet() {
      return { file: [] };
    }
    exports2.FileDescriptorSet = {
      encode(message, writer = new wire_1.BinaryWriter()) {
        for (const v of message.file) {
          exports2.FileDescriptorProto.encode(v, writer.uint32(10).fork()).join();
        }
        if (message._unknownFields !== void 0) {
          for (const [key, values] of Object.entries(message._unknownFields)) {
            const tag = parseInt(key, 10);
            for (const value of values) {
              writer.uint32(tag).raw(value);
            }
          }
        }
        return writer;
      },
      setExtension(message, extension, value) {
        const encoded = extension.encode(value);
        if (message._unknownFields !== void 0) {
          delete message._unknownFields[extension.tag];
          if (extension.singularTag !== void 0) {
            delete message._unknownFields[extension.singularTag];
          }
        }
        if (encoded.length !== 0) {
          if (message._unknownFields === void 0) {
            message._unknownFields = {};
          }
          message._unknownFields[extension.tag] = encoded;
        }
      },
      decode(input, length) {
        const reader = input instanceof wire_1.BinaryReader ? input : new wire_1.BinaryReader(input);
        let end = length === void 0 ? reader.len : reader.pos + length;
        const message = Object.create(createBaseFileDescriptorSet());
        while (reader.pos < end) {
          const tag = reader.uint32();
          switch (tag >>> 3) {
            case 1:
              if (tag !== 10) {
                break;
              }
              message.file.push(exports2.FileDescriptorProto.decode(reader, reader.uint32()));
              continue;
          }
          if ((tag & 7) === 4 || tag === 0) {
            break;
          }
          const buf = reader.skip(tag & 7);
          if (message._unknownFields === void 0) {
            message._unknownFields = {};
          }
          const list = message._unknownFields[tag];
          if (list === void 0) {
            message._unknownFields[tag] = [buf];
          } else {
            list.push(buf);
          }
        }
        return message;
      },
      getExtension(message, extension) {
        let results = void 0;
        if (message._unknownFields === void 0) {
          return void 0;
        }
        let list = message._unknownFields[extension.tag];
        if (list !== void 0) {
          results = extension.decode(extension.tag, list);
        }
        if (extension.singularTag === void 0) {
          return results;
        }
        list = message._unknownFields[extension.singularTag];
        if (list !== void 0) {
          const results2 = extension.decode(extension.singularTag, list);
          if (results !== void 0 && results.length !== 0) {
            results = results.concat(results2);
          } else {
            results = results2;
          }
        }
        return results;
      },
      fromJSON(object) {
        return {
          file: globalThis.Array.isArray(object?.file) ? object.file.map((e) => exports2.FileDescriptorProto.fromJSON(e)) : []
        };
      },
      toJSON(message) {
        const obj = {};
        if (message.file?.length) {
          obj.file = message.file.map((e) => exports2.FileDescriptorProto.toJSON(e));
        }
        return obj;
      },
      create(base) {
        return exports2.FileDescriptorSet.fromPartial(base ?? {});
      },
      fromPartial(object) {
        const message = Object.create(createBaseFileDescriptorSet());
        message.file = object.file?.map((e) => exports2.FileDescriptorProto.fromPartial(e)) || [];
        return message;
      }
    };
    function createBaseFileDescriptorProto() {
      return {
        name: "",
        package: "",
        dependency: [],
        publicDependency: [],
        weakDependency: [],
        optionDependency: [],
        messageType: [],
        enumType: [],
        service: [],
        extension: [],
        options: void 0,
        sourceCodeInfo: void 0,
        syntax: "",
        edition: 0
      };
    }
    exports2.FileDescriptorProto = {
      encode(message, writer = new wire_1.BinaryWriter()) {
        if (message.name !== "") {
          writer.uint32(10).string(message.name);
        }
        if (message.package !== "") {
          writer.uint32(18).string(message.package);
        }
        for (const v of message.dependency) {
          writer.uint32(26).string(v);
        }
        writer.uint32(82).fork();
        for (const v of message.publicDependency) {
          writer.int32(v);
        }
        writer.join();
        writer.uint32(90).fork();
        for (const v of message.weakDependency) {
          writer.int32(v);
        }
        writer.join();
        for (const v of message.optionDependency) {
          writer.uint32(122).string(v);
        }
        for (const v of message.messageType) {
          exports2.DescriptorProto.encode(v, writer.uint32(34).fork()).join();
        }
        for (const v of message.enumType) {
          exports2.EnumDescriptorProto.encode(v, writer.uint32(42).fork()).join();
        }
        for (const v of message.service) {
          exports2.ServiceDescriptorProto.encode(v, writer.uint32(50).fork()).join();
        }
        for (const v of message.extension) {
          exports2.FieldDescriptorProto.encode(v, writer.uint32(58).fork()).join();
        }
        if (message.options !== void 0) {
          exports2.FileOptions.encode(message.options, writer.uint32(66).fork()).join();
        }
        if (message.sourceCodeInfo !== void 0) {
          exports2.SourceCodeInfo.encode(message.sourceCodeInfo, writer.uint32(74).fork()).join();
        }
        if (message.syntax !== "") {
          writer.uint32(98).string(message.syntax);
        }
        if (message.edition !== 0) {
          writer.uint32(112).int32(message.edition);
        }
        if (message._unknownFields !== void 0) {
          for (const [key, values] of Object.entries(message._unknownFields)) {
            const tag = parseInt(key, 10);
            for (const value of values) {
              writer.uint32(tag).raw(value);
            }
          }
        }
        return writer;
      },
      decode(input, length) {
        const reader = input instanceof wire_1.BinaryReader ? input : new wire_1.BinaryReader(input);
        let end = length === void 0 ? reader.len : reader.pos + length;
        const message = Object.create(createBaseFileDescriptorProto());
        while (reader.pos < end) {
          const tag = reader.uint32();
          switch (tag >>> 3) {
            case 1:
              if (tag !== 10) {
                break;
              }
              message.name = reader.string();
              continue;
            case 2:
              if (tag !== 18) {
                break;
              }
              message.package = reader.string();
              continue;
            case 3:
              if (tag !== 26) {
                break;
              }
              message.dependency.push(reader.string());
              continue;
            case 10:
              if (tag === 80) {
                message.publicDependency.push(reader.int32());
                continue;
              }
              if (tag === 82) {
                const end2 = reader.uint32() + reader.pos;
                while (reader.pos < end2) {
                  message.publicDependency.push(reader.int32());
                }
                continue;
              }
              break;
            case 11:
              if (tag === 88) {
                message.weakDependency.push(reader.int32());
                continue;
              }
              if (tag === 90) {
                const end2 = reader.uint32() + reader.pos;
                while (reader.pos < end2) {
                  message.weakDependency.push(reader.int32());
                }
                continue;
              }
              break;
            case 15:
              if (tag !== 122) {
                break;
              }
              message.optionDependency.push(reader.string());
              continue;
            case 4:
              if (tag !== 34) {
                break;
              }
              message.messageType.push(exports2.DescriptorProto.decode(reader, reader.uint32()));
              continue;
            case 5:
              if (tag !== 42) {
                break;
              }
              message.enumType.push(exports2.EnumDescriptorProto.decode(reader, reader.uint32()));
              continue;
            case 6:
              if (tag !== 50) {
                break;
              }
              message.service.push(exports2.ServiceDescriptorProto.decode(reader, reader.uint32()));
              continue;
            case 7:
              if (tag !== 58) {
                break;
              }
              message.extension.push(exports2.FieldDescriptorProto.decode(reader, reader.uint32()));
              continue;
            case 8:
              if (tag !== 66) {
                break;
              }
              message.options = exports2.FileOptions.decode(reader, reader.uint32());
              continue;
            case 9:
              if (tag !== 74) {
                break;
              }
              message.sourceCodeInfo = exports2.SourceCodeInfo.decode(reader, reader.uint32());
              continue;
            case 12:
              if (tag !== 98) {
                break;
              }
              message.syntax = reader.string();
              continue;
            case 14:
              if (tag !== 112) {
                break;
              }
              message.edition = reader.int32();
              continue;
          }
          if ((tag & 7) === 4 || tag === 0) {
            break;
          }
          const buf = reader.skip(tag & 7);
          if (message._unknownFields === void 0) {
            message._unknownFields = {};
          }
          const list = message._unknownFields[tag];
          if (list === void 0) {
            message._unknownFields[tag] = [buf];
          } else {
            list.push(buf);
          }
        }
        return message;
      },
      fromJSON(object) {
        return {
          name: isSet(object.name) ? globalThis.String(object.name) : "",
          package: isSet(object.package) ? globalThis.String(object.package) : "",
          dependency: globalThis.Array.isArray(object?.dependency) ? object.dependency.map((e) => globalThis.String(e)) : [],
          publicDependency: globalThis.Array.isArray(object?.publicDependency) ? object.publicDependency.map((e) => globalThis.Number(e)) : [],
          weakDependency: globalThis.Array.isArray(object?.weakDependency) ? object.weakDependency.map((e) => globalThis.Number(e)) : [],
          optionDependency: globalThis.Array.isArray(object?.optionDependency) ? object.optionDependency.map((e) => globalThis.String(e)) : [],
          messageType: globalThis.Array.isArray(object?.messageType) ? object.messageType.map((e) => exports2.DescriptorProto.fromJSON(e)) : [],
          enumType: globalThis.Array.isArray(object?.enumType) ? object.enumType.map((e) => exports2.EnumDescriptorProto.fromJSON(e)) : [],
          service: globalThis.Array.isArray(object?.service) ? object.service.map((e) => exports2.ServiceDescriptorProto.fromJSON(e)) : [],
          extension: globalThis.Array.isArray(object?.extension) ? object.extension.map((e) => exports2.FieldDescriptorProto.fromJSON(e)) : [],
          options: isSet(object.options) ? exports2.FileOptions.fromJSON(object.options) : void 0,
          sourceCodeInfo: isSet(object.sourceCodeInfo) ? exports2.SourceCodeInfo.fromJSON(object.sourceCodeInfo) : void 0,
          syntax: isSet(object.syntax) ? globalThis.String(object.syntax) : "",
          edition: isSet(object.edition) ? editionFromJSON(object.edition) : 0
        };
      },
      toJSON(message) {
        const obj = {};
        if (message.name !== "") {
          obj.name = message.name;
        }
        if (message.package !== "") {
          obj.package = message.package;
        }
        if (message.dependency?.length) {
          obj.dependency = message.dependency;
        }
        if (message.publicDependency?.length) {
          obj.publicDependency = message.publicDependency.map((e) => Math.round(e));
        }
        if (message.weakDependency?.length) {
          obj.weakDependency = message.weakDependency.map((e) => Math.round(e));
        }
        if (message.optionDependency?.length) {
          obj.optionDependency = message.optionDependency;
        }
        if (message.messageType?.length) {
          obj.messageType = message.messageType.map((e) => exports2.DescriptorProto.toJSON(e));
        }
        if (message.enumType?.length) {
          obj.enumType = message.enumType.map((e) => exports2.EnumDescriptorProto.toJSON(e));
        }
        if (message.service?.length) {
          obj.service = message.service.map((e) => exports2.ServiceDescriptorProto.toJSON(e));
        }
        if (message.extension?.length) {
          obj.extension = message.extension.map((e) => exports2.FieldDescriptorProto.toJSON(e));
        }
        if (message.options !== void 0) {
          obj.options = exports2.FileOptions.toJSON(message.options);
        }
        if (message.sourceCodeInfo !== void 0) {
          obj.sourceCodeInfo = exports2.SourceCodeInfo.toJSON(message.sourceCodeInfo);
        }
        if (message.syntax !== "") {
          obj.syntax = message.syntax;
        }
        if (message.edition !== 0) {
          obj.edition = editionToJSON(message.edition);
        }
        return obj;
      },
      create(base) {
        return exports2.FileDescriptorProto.fromPartial(base ?? {});
      },
      fromPartial(object) {
        const message = Object.create(createBaseFileDescriptorProto());
        message.name = object.name ?? "";
        message.package = object.package ?? "";
        message.dependency = object.dependency?.map((e) => e) || [];
        message.publicDependency = object.publicDependency?.map((e) => e) || [];
        message.weakDependency = object.weakDependency?.map((e) => e) || [];
        message.optionDependency = object.optionDependency?.map((e) => e) || [];
        message.messageType = object.messageType?.map((e) => exports2.DescriptorProto.fromPartial(e)) || [];
        message.enumType = object.enumType?.map((e) => exports2.EnumDescriptorProto.fromPartial(e)) || [];
        message.service = object.service?.map((e) => exports2.ServiceDescriptorProto.fromPartial(e)) || [];
        message.extension = object.extension?.map((e) => exports2.FieldDescriptorProto.fromPartial(e)) || [];
        message.options = object.options !== void 0 && object.options !== null ? exports2.FileOptions.fromPartial(object.options) : void 0;
        message.sourceCodeInfo = object.sourceCodeInfo !== void 0 && object.sourceCodeInfo !== null ? exports2.SourceCodeInfo.fromPartial(object.sourceCodeInfo) : void 0;
        message.syntax = object.syntax ?? "";
        message.edition = object.edition ?? 0;
        return message;
      }
    };
    function createBaseDescriptorProto() {
      return {
        name: "",
        field: [],
        extension: [],
        nestedType: [],
        enumType: [],
        extensionRange: [],
        oneofDecl: [],
        options: void 0,
        reservedRange: [],
        reservedName: [],
        visibility: 0
      };
    }
    exports2.DescriptorProto = {
      encode(message, writer = new wire_1.BinaryWriter()) {
        if (message.name !== "") {
          writer.uint32(10).string(message.name);
        }
        for (const v of message.field) {
          exports2.FieldDescriptorProto.encode(v, writer.uint32(18).fork()).join();
        }
        for (const v of message.extension) {
          exports2.FieldDescriptorProto.encode(v, writer.uint32(50).fork()).join();
        }
        for (const v of message.nestedType) {
          exports2.DescriptorProto.encode(v, writer.uint32(26).fork()).join();
        }
        for (const v of message.enumType) {
          exports2.EnumDescriptorProto.encode(v, writer.uint32(34).fork()).join();
        }
        for (const v of message.extensionRange) {
          exports2.DescriptorProto_ExtensionRange.encode(v, writer.uint32(42).fork()).join();
        }
        for (const v of message.oneofDecl) {
          exports2.OneofDescriptorProto.encode(v, writer.uint32(66).fork()).join();
        }
        if (message.options !== void 0) {
          exports2.MessageOptions.encode(message.options, writer.uint32(58).fork()).join();
        }
        for (const v of message.reservedRange) {
          exports2.DescriptorProto_ReservedRange.encode(v, writer.uint32(74).fork()).join();
        }
        for (const v of message.reservedName) {
          writer.uint32(82).string(v);
        }
        if (message.visibility !== 0) {
          writer.uint32(88).int32(message.visibility);
        }
        if (message._unknownFields !== void 0) {
          for (const [key, values] of Object.entries(message._unknownFields)) {
            const tag = parseInt(key, 10);
            for (const value of values) {
              writer.uint32(tag).raw(value);
            }
          }
        }
        return writer;
      },
      decode(input, length) {
        const reader = input instanceof wire_1.BinaryReader ? input : new wire_1.BinaryReader(input);
        let end = length === void 0 ? reader.len : reader.pos + length;
        const message = Object.create(createBaseDescriptorProto());
        while (reader.pos < end) {
          const tag = reader.uint32();
          switch (tag >>> 3) {
            case 1:
              if (tag !== 10) {
                break;
              }
              message.name = reader.string();
              continue;
            case 2:
              if (tag !== 18) {
                break;
              }
              message.field.push(exports2.FieldDescriptorProto.decode(reader, reader.uint32()));
              continue;
            case 6:
              if (tag !== 50) {
                break;
              }
              message.extension.push(exports2.FieldDescriptorProto.decode(reader, reader.uint32()));
              continue;
            case 3:
              if (tag !== 26) {
                break;
              }
              message.nestedType.push(exports2.DescriptorProto.decode(reader, reader.uint32()));
              continue;
            case 4:
              if (tag !== 34) {
                break;
              }
              message.enumType.push(exports2.EnumDescriptorProto.decode(reader, reader.uint32()));
              continue;
            case 5:
              if (tag !== 42) {
                break;
              }
              message.extensionRange.push(exports2.DescriptorProto_ExtensionRange.decode(reader, reader.uint32()));
              continue;
            case 8:
              if (tag !== 66) {
                break;
              }
              message.oneofDecl.push(exports2.OneofDescriptorProto.decode(reader, reader.uint32()));
              continue;
            case 7:
              if (tag !== 58) {
                break;
              }
              message.options = exports2.MessageOptions.decode(reader, reader.uint32());
              continue;
            case 9:
              if (tag !== 74) {
                break;
              }
              message.reservedRange.push(exports2.DescriptorProto_ReservedRange.decode(reader, reader.uint32()));
              continue;
            case 10:
              if (tag !== 82) {
                break;
              }
              message.reservedName.push(reader.string());
              continue;
            case 11:
              if (tag !== 88) {
                break;
              }
              message.visibility = reader.int32();
              continue;
          }
          if ((tag & 7) === 4 || tag === 0) {
            break;
          }
          const buf = reader.skip(tag & 7);
          if (message._unknownFields === void 0) {
            message._unknownFields = {};
          }
          const list = message._unknownFields[tag];
          if (list === void 0) {
            message._unknownFields[tag] = [buf];
          } else {
            list.push(buf);
          }
        }
        return message;
      },
      fromJSON(object) {
        return {
          name: isSet(object.name) ? globalThis.String(object.name) : "",
          field: globalThis.Array.isArray(object?.field) ? object.field.map((e) => exports2.FieldDescriptorProto.fromJSON(e)) : [],
          extension: globalThis.Array.isArray(object?.extension) ? object.extension.map((e) => exports2.FieldDescriptorProto.fromJSON(e)) : [],
          nestedType: globalThis.Array.isArray(object?.nestedType) ? object.nestedType.map((e) => exports2.DescriptorProto.fromJSON(e)) : [],
          enumType: globalThis.Array.isArray(object?.enumType) ? object.enumType.map((e) => exports2.EnumDescriptorProto.fromJSON(e)) : [],
          extensionRange: globalThis.Array.isArray(object?.extensionRange) ? object.extensionRange.map((e) => exports2.DescriptorProto_ExtensionRange.fromJSON(e)) : [],
          oneofDecl: globalThis.Array.isArray(object?.oneofDecl) ? object.oneofDecl.map((e) => exports2.OneofDescriptorProto.fromJSON(e)) : [],
          options: isSet(object.options) ? exports2.MessageOptions.fromJSON(object.options) : void 0,
          reservedRange: globalThis.Array.isArray(object?.reservedRange) ? object.reservedRange.map((e) => exports2.DescriptorProto_ReservedRange.fromJSON(e)) : [],
          reservedName: globalThis.Array.isArray(object?.reservedName) ? object.reservedName.map((e) => globalThis.String(e)) : [],
          visibility: isSet(object.visibility) ? symbolVisibilityFromJSON(object.visibility) : 0
        };
      },
      toJSON(message) {
        const obj = {};
        if (message.name !== "") {
          obj.name = message.name;
        }
        if (message.field?.length) {
          obj.field = message.field.map((e) => exports2.FieldDescriptorProto.toJSON(e));
        }
        if (message.extension?.length) {
          obj.extension = message.extension.map((e) => exports2.FieldDescriptorProto.toJSON(e));
        }
        if (message.nestedType?.length) {
          obj.nestedType = message.nestedType.map((e) => exports2.DescriptorProto.toJSON(e));
        }
        if (message.enumType?.length) {
          obj.enumType = message.enumType.map((e) => exports2.EnumDescriptorProto.toJSON(e));
        }
        if (message.extensionRange?.length) {
          obj.extensionRange = message.extensionRange.map((e) => exports2.DescriptorProto_ExtensionRange.toJSON(e));
        }
        if (message.oneofDecl?.length) {
          obj.oneofDecl = message.oneofDecl.map((e) => exports2.OneofDescriptorProto.toJSON(e));
        }
        if (message.options !== void 0) {
          obj.options = exports2.MessageOptions.toJSON(message.options);
        }
        if (message.reservedRange?.length) {
          obj.reservedRange = message.reservedRange.map((e) => exports2.DescriptorProto_ReservedRange.toJSON(e));
        }
        if (message.reservedName?.length) {
          obj.reservedName = message.reservedName;
        }
        if (message.visibility !== 0) {
          obj.visibility = symbolVisibilityToJSON(message.visibility);
        }
        return obj;
      },
      create(base) {
        return exports2.DescriptorProto.fromPartial(base ?? {});
      },
      fromPartial(object) {
        const message = Object.create(createBaseDescriptorProto());
        message.name = object.name ?? "";
        message.field = object.field?.map((e) => exports2.FieldDescriptorProto.fromPartial(e)) || [];
        message.extension = object.extension?.map((e) => exports2.FieldDescriptorProto.fromPartial(e)) || [];
        message.nestedType = object.nestedType?.map((e) => exports2.DescriptorProto.fromPartial(e)) || [];
        message.enumType = object.enumType?.map((e) => exports2.EnumDescriptorProto.fromPartial(e)) || [];
        message.extensionRange = object.extensionRange?.map((e) => exports2.DescriptorProto_ExtensionRange.fromPartial(e)) || [];
        message.oneofDecl = object.oneofDecl?.map((e) => exports2.OneofDescriptorProto.fromPartial(e)) || [];
        message.options = object.options !== void 0 && object.options !== null ? exports2.MessageOptions.fromPartial(object.options) : void 0;
        message.reservedRange = object.reservedRange?.map((e) => exports2.DescriptorProto_ReservedRange.fromPartial(e)) || [];
        message.reservedName = object.reservedName?.map((e) => e) || [];
        message.visibility = object.visibility ?? 0;
        return message;
      }
    };
    function createBaseDescriptorProto_ExtensionRange() {
      return { start: 0, end: 0, options: void 0 };
    }
    exports2.DescriptorProto_ExtensionRange = {
      encode(message, writer = new wire_1.BinaryWriter()) {
        if (message.start !== 0) {
          writer.uint32(8).int32(message.start);
        }
        if (message.end !== 0) {
          writer.uint32(16).int32(message.end);
        }
        if (message.options !== void 0) {
          exports2.ExtensionRangeOptions.encode(message.options, writer.uint32(26).fork()).join();
        }
        if (message._unknownFields !== void 0) {
          for (const [key, values] of Object.entries(message._unknownFields)) {
            const tag = parseInt(key, 10);
            for (const value of values) {
              writer.uint32(tag).raw(value);
            }
          }
        }
        return writer;
      },
      decode(input, length) {
        const reader = input instanceof wire_1.BinaryReader ? input : new wire_1.BinaryReader(input);
        let end = length === void 0 ? reader.len : reader.pos + length;
        const message = Object.create(createBaseDescriptorProto_ExtensionRange());
        while (reader.pos < end) {
          const tag = reader.uint32();
          switch (tag >>> 3) {
            case 1:
              if (tag !== 8) {
                break;
              }
              message.start = reader.int32();
              continue;
            case 2:
              if (tag !== 16) {
                break;
              }
              message.end = reader.int32();
              continue;
            case 3:
              if (tag !== 26) {
                break;
              }
              message.options = exports2.ExtensionRangeOptions.decode(reader, reader.uint32());
              continue;
          }
          if ((tag & 7) === 4 || tag === 0) {
            break;
          }
          const buf = reader.skip(tag & 7);
          if (message._unknownFields === void 0) {
            message._unknownFields = {};
          }
          const list = message._unknownFields[tag];
          if (list === void 0) {
            message._unknownFields[tag] = [buf];
          } else {
            list.push(buf);
          }
        }
        return message;
      },
      fromJSON(object) {
        return {
          start: isSet(object.start) ? globalThis.Number(object.start) : 0,
          end: isSet(object.end) ? globalThis.Number(object.end) : 0,
          options: isSet(object.options) ? exports2.ExtensionRangeOptions.fromJSON(object.options) : void 0
        };
      },
      toJSON(message) {
        const obj = {};
        if (message.start !== 0) {
          obj.start = Math.round(message.start);
        }
        if (message.end !== 0) {
          obj.end = Math.round(message.end);
        }
        if (message.options !== void 0) {
          obj.options = exports2.ExtensionRangeOptions.toJSON(message.options);
        }
        return obj;
      },
      create(base) {
        return exports2.DescriptorProto_ExtensionRange.fromPartial(base ?? {});
      },
      fromPartial(object) {
        const message = Object.create(createBaseDescriptorProto_ExtensionRange());
        message.start = object.start ?? 0;
        message.end = object.end ?? 0;
        message.options = object.options !== void 0 && object.options !== null ? exports2.ExtensionRangeOptions.fromPartial(object.options) : void 0;
        return message;
      }
    };
    function createBaseDescriptorProto_ReservedRange() {
      return { start: 0, end: 0 };
    }
    exports2.DescriptorProto_ReservedRange = {
      encode(message, writer = new wire_1.BinaryWriter()) {
        if (message.start !== 0) {
          writer.uint32(8).int32(message.start);
        }
        if (message.end !== 0) {
          writer.uint32(16).int32(message.end);
        }
        if (message._unknownFields !== void 0) {
          for (const [key, values] of Object.entries(message._unknownFields)) {
            const tag = parseInt(key, 10);
            for (const value of values) {
              writer.uint32(tag).raw(value);
            }
          }
        }
        return writer;
      },
      decode(input, length) {
        const reader = input instanceof wire_1.BinaryReader ? input : new wire_1.BinaryReader(input);
        let end = length === void 0 ? reader.len : reader.pos + length;
        const message = Object.create(createBaseDescriptorProto_ReservedRange());
        while (reader.pos < end) {
          const tag = reader.uint32();
          switch (tag >>> 3) {
            case 1:
              if (tag !== 8) {
                break;
              }
              message.start = reader.int32();
              continue;
            case 2:
              if (tag !== 16) {
                break;
              }
              message.end = reader.int32();
              continue;
          }
          if ((tag & 7) === 4 || tag === 0) {
            break;
          }
          const buf = reader.skip(tag & 7);
          if (message._unknownFields === void 0) {
            message._unknownFields = {};
          }
          const list = message._unknownFields[tag];
          if (list === void 0) {
            message._unknownFields[tag] = [buf];
          } else {
            list.push(buf);
          }
        }
        return message;
      },
      fromJSON(object) {
        return {
          start: isSet(object.start) ? globalThis.Number(object.start) : 0,
          end: isSet(object.end) ? globalThis.Number(object.end) : 0
        };
      },
      toJSON(message) {
        const obj = {};
        if (message.start !== 0) {
          obj.start = Math.round(message.start);
        }
        if (message.end !== 0) {
          obj.end = Math.round(message.end);
        }
        return obj;
      },
      create(base) {
        return exports2.DescriptorProto_ReservedRange.fromPartial(base ?? {});
      },
      fromPartial(object) {
        const message = Object.create(createBaseDescriptorProto_ReservedRange());
        message.start = object.start ?? 0;
        message.end = object.end ?? 0;
        return message;
      }
    };
    function createBaseExtensionRangeOptions() {
      return { uninterpretedOption: [], declaration: [], features: void 0, verification: 1 };
    }
    exports2.ExtensionRangeOptions = {
      encode(message, writer = new wire_1.BinaryWriter()) {
        for (const v of message.uninterpretedOption) {
          exports2.UninterpretedOption.encode(v, writer.uint32(7994).fork()).join();
        }
        for (const v of message.declaration) {
          exports2.ExtensionRangeOptions_Declaration.encode(v, writer.uint32(18).fork()).join();
        }
        if (message.features !== void 0) {
          exports2.FeatureSet.encode(message.features, writer.uint32(402).fork()).join();
        }
        if (message.verification !== 1) {
          writer.uint32(24).int32(message.verification);
        }
        if (message._unknownFields !== void 0) {
          for (const [key, values] of Object.entries(message._unknownFields)) {
            const tag = parseInt(key, 10);
            for (const value of values) {
              writer.uint32(tag).raw(value);
            }
          }
        }
        return writer;
      },
      setExtension(message, extension, value) {
        const encoded = extension.encode(value);
        if (message._unknownFields !== void 0) {
          delete message._unknownFields[extension.tag];
          if (extension.singularTag !== void 0) {
            delete message._unknownFields[extension.singularTag];
          }
        }
        if (encoded.length !== 0) {
          if (message._unknownFields === void 0) {
            message._unknownFields = {};
          }
          message._unknownFields[extension.tag] = encoded;
        }
      },
      decode(input, length) {
        const reader = input instanceof wire_1.BinaryReader ? input : new wire_1.BinaryReader(input);
        let end = length === void 0 ? reader.len : reader.pos + length;
        const message = Object.create(createBaseExtensionRangeOptions());
        while (reader.pos < end) {
          const tag = reader.uint32();
          switch (tag >>> 3) {
            case 999:
              if (tag !== 7994) {
                break;
              }
              message.uninterpretedOption.push(exports2.UninterpretedOption.decode(reader, reader.uint32()));
              continue;
            case 2:
              if (tag !== 18) {
                break;
              }
              message.declaration.push(exports2.ExtensionRangeOptions_Declaration.decode(reader, reader.uint32()));
              continue;
            case 50:
              if (tag !== 402) {
                break;
              }
              message.features = exports2.FeatureSet.decode(reader, reader.uint32());
              continue;
            case 3:
              if (tag !== 24) {
                break;
              }
              message.verification = reader.int32();
              continue;
          }
          if ((tag & 7) === 4 || tag === 0) {
            break;
          }
          const buf = reader.skip(tag & 7);
          if (message._unknownFields === void 0) {
            message._unknownFields = {};
          }
          const list = message._unknownFields[tag];
          if (list === void 0) {
            message._unknownFields[tag] = [buf];
          } else {
            list.push(buf);
          }
        }
        return message;
      },
      getExtension(message, extension) {
        let results = void 0;
        if (message._unknownFields === void 0) {
          return void 0;
        }
        let list = message._unknownFields[extension.tag];
        if (list !== void 0) {
          results = extension.decode(extension.tag, list);
        }
        if (extension.singularTag === void 0) {
          return results;
        }
        list = message._unknownFields[extension.singularTag];
        if (list !== void 0) {
          const results2 = extension.decode(extension.singularTag, list);
          if (results !== void 0 && results.length !== 0) {
            results = results.concat(results2);
          } else {
            results = results2;
          }
        }
        return results;
      },
      fromJSON(object) {
        return {
          uninterpretedOption: globalThis.Array.isArray(object?.uninterpretedOption) ? object.uninterpretedOption.map((e) => exports2.UninterpretedOption.fromJSON(e)) : [],
          declaration: globalThis.Array.isArray(object?.declaration) ? object.declaration.map((e) => exports2.ExtensionRangeOptions_Declaration.fromJSON(e)) : [],
          features: isSet(object.features) ? exports2.FeatureSet.fromJSON(object.features) : void 0,
          verification: isSet(object.verification) ? extensionRangeOptions_VerificationStateFromJSON(object.verification) : 1
        };
      },
      toJSON(message) {
        const obj = {};
        if (message.uninterpretedOption?.length) {
          obj.uninterpretedOption = message.uninterpretedOption.map((e) => exports2.UninterpretedOption.toJSON(e));
        }
        if (message.declaration?.length) {
          obj.declaration = message.declaration.map((e) => exports2.ExtensionRangeOptions_Declaration.toJSON(e));
        }
        if (message.features !== void 0) {
          obj.features = exports2.FeatureSet.toJSON(message.features);
        }
        if (message.verification !== 1) {
          obj.verification = extensionRangeOptions_VerificationStateToJSON(message.verification);
        }
        return obj;
      },
      create(base) {
        return exports2.ExtensionRangeOptions.fromPartial(base ?? {});
      },
      fromPartial(object) {
        const message = Object.create(createBaseExtensionRangeOptions());
        message.uninterpretedOption = object.uninterpretedOption?.map((e) => exports2.UninterpretedOption.fromPartial(e)) || [];
        message.declaration = object.declaration?.map((e) => exports2.ExtensionRangeOptions_Declaration.fromPartial(e)) || [];
        message.features = object.features !== void 0 && object.features !== null ? exports2.FeatureSet.fromPartial(object.features) : void 0;
        message.verification = object.verification ?? 1;
        return message;
      }
    };
    function createBaseExtensionRangeOptions_Declaration() {
      return { number: 0, fullName: "", type: "", reserved: false, repeated: false };
    }
    exports2.ExtensionRangeOptions_Declaration = {
      encode(message, writer = new wire_1.BinaryWriter()) {
        if (message.number !== 0) {
          writer.uint32(8).int32(message.number);
        }
        if (message.fullName !== "") {
          writer.uint32(18).string(message.fullName);
        }
        if (message.type !== "") {
          writer.uint32(26).string(message.type);
        }
        if (message.reserved !== false) {
          writer.uint32(40).bool(message.reserved);
        }
        if (message.repeated !== false) {
          writer.uint32(48).bool(message.repeated);
        }
        if (message._unknownFields !== void 0) {
          for (const [key, values] of Object.entries(message._unknownFields)) {
            const tag = parseInt(key, 10);
            for (const value of values) {
              writer.uint32(tag).raw(value);
            }
          }
        }
        return writer;
      },
      decode(input, length) {
        const reader = input instanceof wire_1.BinaryReader ? input : new wire_1.BinaryReader(input);
        let end = length === void 0 ? reader.len : reader.pos + length;
        const message = Object.create(createBaseExtensionRangeOptions_Declaration());
        while (reader.pos < end) {
          const tag = reader.uint32();
          switch (tag >>> 3) {
            case 1:
              if (tag !== 8) {
                break;
              }
              message.number = reader.int32();
              continue;
            case 2:
              if (tag !== 18) {
                break;
              }
              message.fullName = reader.string();
              continue;
            case 3:
              if (tag !== 26) {
                break;
              }
              message.type = reader.string();
              continue;
            case 5:
              if (tag !== 40) {
                break;
              }
              message.reserved = reader.bool();
              continue;
            case 6:
              if (tag !== 48) {
                break;
              }
              message.repeated = reader.bool();
              continue;
          }
          if ((tag & 7) === 4 || tag === 0) {
            break;
          }
          const buf = reader.skip(tag & 7);
          if (message._unknownFields === void 0) {
            message._unknownFields = {};
          }
          const list = message._unknownFields[tag];
          if (list === void 0) {
            message._unknownFields[tag] = [buf];
          } else {
            list.push(buf);
          }
        }
        return message;
      },
      fromJSON(object) {
        return {
          number: isSet(object.number) ? globalThis.Number(object.number) : 0,
          fullName: isSet(object.fullName) ? globalThis.String(object.fullName) : "",
          type: isSet(object.type) ? globalThis.String(object.type) : "",
          reserved: isSet(object.reserved) ? globalThis.Boolean(object.reserved) : false,
          repeated: isSet(object.repeated) ? globalThis.Boolean(object.repeated) : false
        };
      },
      toJSON(message) {
        const obj = {};
        if (message.number !== 0) {
          obj.number = Math.round(message.number);
        }
        if (message.fullName !== "") {
          obj.fullName = message.fullName;
        }
        if (message.type !== "") {
          obj.type = message.type;
        }
        if (message.reserved !== false) {
          obj.reserved = message.reserved;
        }
        if (message.repeated !== false) {
          obj.repeated = message.repeated;
        }
        return obj;
      },
      create(base) {
        return exports2.ExtensionRangeOptions_Declaration.fromPartial(base ?? {});
      },
      fromPartial(object) {
        const message = Object.create(createBaseExtensionRangeOptions_Declaration());
        message.number = object.number ?? 0;
        message.fullName = object.fullName ?? "";
        message.type = object.type ?? "";
        message.reserved = object.reserved ?? false;
        message.repeated = object.repeated ?? false;
        return message;
      }
    };
    function createBaseFieldDescriptorProto() {
      return {
        name: "",
        number: 0,
        label: 1,
        type: 1,
        typeName: "",
        extendee: "",
        defaultValue: "",
        oneofIndex: 0,
        jsonName: "",
        options: void 0,
        proto3Optional: false
      };
    }
    exports2.FieldDescriptorProto = {
      encode(message, writer = new wire_1.BinaryWriter()) {
        if (message.name !== "") {
          writer.uint32(10).string(message.name);
        }
        if (message.number !== 0) {
          writer.uint32(24).int32(message.number);
        }
        if (message.label !== 1) {
          writer.uint32(32).int32(message.label);
        }
        if (message.type !== 1) {
          writer.uint32(40).int32(message.type);
        }
        if (message.typeName !== "") {
          writer.uint32(50).string(message.typeName);
        }
        if (message.extendee !== "") {
          writer.uint32(18).string(message.extendee);
        }
        if (message.defaultValue !== "") {
          writer.uint32(58).string(message.defaultValue);
        }
        if (message.oneofIndex !== 0) {
          writer.uint32(72).int32(message.oneofIndex);
        }
        if (message.jsonName !== "") {
          writer.uint32(82).string(message.jsonName);
        }
        if (message.options !== void 0) {
          exports2.FieldOptions.encode(message.options, writer.uint32(66).fork()).join();
        }
        if (message.proto3Optional !== false) {
          writer.uint32(136).bool(message.proto3Optional);
        }
        if (message._unknownFields !== void 0) {
          for (const [key, values] of Object.entries(message._unknownFields)) {
            const tag = parseInt(key, 10);
            for (const value of values) {
              writer.uint32(tag).raw(value);
            }
          }
        }
        return writer;
      },
      decode(input, length) {
        const reader = input instanceof wire_1.BinaryReader ? input : new wire_1.BinaryReader(input);
        let end = length === void 0 ? reader.len : reader.pos + length;
        const message = Object.create(createBaseFieldDescriptorProto());
        while (reader.pos < end) {
          const tag = reader.uint32();
          switch (tag >>> 3) {
            case 1:
              if (tag !== 10) {
                break;
              }
              message.name = reader.string();
              continue;
            case 3:
              if (tag !== 24) {
                break;
              }
              message.number = reader.int32();
              continue;
            case 4:
              if (tag !== 32) {
                break;
              }
              message.label = reader.int32();
              continue;
            case 5:
              if (tag !== 40) {
                break;
              }
              message.type = reader.int32();
              continue;
            case 6:
              if (tag !== 50) {
                break;
              }
              message.typeName = reader.string();
              continue;
            case 2:
              if (tag !== 18) {
                break;
              }
              message.extendee = reader.string();
              continue;
            case 7:
              if (tag !== 58) {
                break;
              }
              message.defaultValue = reader.string();
              continue;
            case 9:
              if (tag !== 72) {
                break;
              }
              message.oneofIndex = reader.int32();
              continue;
            case 10:
              if (tag !== 82) {
                break;
              }
              message.jsonName = reader.string();
              continue;
            case 8:
              if (tag !== 66) {
                break;
              }
              message.options = exports2.FieldOptions.decode(reader, reader.uint32());
              continue;
            case 17:
              if (tag !== 136) {
                break;
              }
              message.proto3Optional = reader.bool();
              continue;
          }
          if ((tag & 7) === 4 || tag === 0) {
            break;
          }
          const buf = reader.skip(tag & 7);
          if (message._unknownFields === void 0) {
            message._unknownFields = {};
          }
          const list = message._unknownFields[tag];
          if (list === void 0) {
            message._unknownFields[tag] = [buf];
          } else {
            list.push(buf);
          }
        }
        return message;
      },
      fromJSON(object) {
        return {
          name: isSet(object.name) ? globalThis.String(object.name) : "",
          number: isSet(object.number) ? globalThis.Number(object.number) : 0,
          label: isSet(object.label) ? fieldDescriptorProto_LabelFromJSON(object.label) : 1,
          type: isSet(object.type) ? fieldDescriptorProto_TypeFromJSON(object.type) : 1,
          typeName: isSet(object.typeName) ? globalThis.String(object.typeName) : "",
          extendee: isSet(object.extendee) ? globalThis.String(object.extendee) : "",
          defaultValue: isSet(object.defaultValue) ? globalThis.String(object.defaultValue) : "",
          oneofIndex: isSet(object.oneofIndex) ? globalThis.Number(object.oneofIndex) : 0,
          jsonName: isSet(object.jsonName) ? globalThis.String(object.jsonName) : "",
          options: isSet(object.options) ? exports2.FieldOptions.fromJSON(object.options) : void 0,
          proto3Optional: isSet(object.proto3Optional) ? globalThis.Boolean(object.proto3Optional) : false
        };
      },
      toJSON(message) {
        const obj = {};
        if (message.name !== "") {
          obj.name = message.name;
        }
        if (message.number !== 0) {
          obj.number = Math.round(message.number);
        }
        if (message.label !== 1) {
          obj.label = fieldDescriptorProto_LabelToJSON(message.label);
        }
        if (message.type !== 1) {
          obj.type = fieldDescriptorProto_TypeToJSON(message.type);
        }
        if (message.typeName !== "") {
          obj.typeName = message.typeName;
        }
        if (message.extendee !== "") {
          obj.extendee = message.extendee;
        }
        if (message.defaultValue !== "") {
          obj.defaultValue = message.defaultValue;
        }
        if (message.oneofIndex !== 0) {
          obj.oneofIndex = Math.round(message.oneofIndex);
        }
        if (message.jsonName !== "") {
          obj.jsonName = message.jsonName;
        }
        if (message.options !== void 0) {
          obj.options = exports2.FieldOptions.toJSON(message.options);
        }
        if (message.proto3Optional !== false) {
          obj.proto3Optional = message.proto3Optional;
        }
        return obj;
      },
      create(base) {
        return exports2.FieldDescriptorProto.fromPartial(base ?? {});
      },
      fromPartial(object) {
        const message = Object.create(createBaseFieldDescriptorProto());
        message.name = object.name ?? "";
        message.number = object.number ?? 0;
        message.label = object.label ?? 1;
        message.type = object.type ?? 1;
        message.typeName = object.typeName ?? "";
        message.extendee = object.extendee ?? "";
        message.defaultValue = object.defaultValue ?? "";
        message.oneofIndex = object.oneofIndex ?? 0;
        message.jsonName = object.jsonName ?? "";
        message.options = object.options !== void 0 && object.options !== null ? exports2.FieldOptions.fromPartial(object.options) : void 0;
        message.proto3Optional = object.proto3Optional ?? false;
        return message;
      }
    };
    function createBaseOneofDescriptorProto() {
      return { name: "", options: void 0 };
    }
    exports2.OneofDescriptorProto = {
      encode(message, writer = new wire_1.BinaryWriter()) {
        if (message.name !== "") {
          writer.uint32(10).string(message.name);
        }
        if (message.options !== void 0) {
          exports2.OneofOptions.encode(message.options, writer.uint32(18).fork()).join();
        }
        if (message._unknownFields !== void 0) {
          for (const [key, values] of Object.entries(message._unknownFields)) {
            const tag = parseInt(key, 10);
            for (const value of values) {
              writer.uint32(tag).raw(value);
            }
          }
        }
        return writer;
      },
      decode(input, length) {
        const reader = input instanceof wire_1.BinaryReader ? input : new wire_1.BinaryReader(input);
        let end = length === void 0 ? reader.len : reader.pos + length;
        const message = Object.create(createBaseOneofDescriptorProto());
        while (reader.pos < end) {
          const tag = reader.uint32();
          switch (tag >>> 3) {
            case 1:
              if (tag !== 10) {
                break;
              }
              message.name = reader.string();
              continue;
            case 2:
              if (tag !== 18) {
                break;
              }
              message.options = exports2.OneofOptions.decode(reader, reader.uint32());
              continue;
          }
          if ((tag & 7) === 4 || tag === 0) {
            break;
          }
          const buf = reader.skip(tag & 7);
          if (message._unknownFields === void 0) {
            message._unknownFields = {};
          }
          const list = message._unknownFields[tag];
          if (list === void 0) {
            message._unknownFields[tag] = [buf];
          } else {
            list.push(buf);
          }
        }
        return message;
      },
      fromJSON(object) {
        return {
          name: isSet(object.name) ? globalThis.String(object.name) : "",
          options: isSet(object.options) ? exports2.OneofOptions.fromJSON(object.options) : void 0
        };
      },
      toJSON(message) {
        const obj = {};
        if (message.name !== "") {
          obj.name = message.name;
        }
        if (message.options !== void 0) {
          obj.options = exports2.OneofOptions.toJSON(message.options);
        }
        return obj;
      },
      create(base) {
        return exports2.OneofDescriptorProto.fromPartial(base ?? {});
      },
      fromPartial(object) {
        const message = Object.create(createBaseOneofDescriptorProto());
        message.name = object.name ?? "";
        message.options = object.options !== void 0 && object.options !== null ? exports2.OneofOptions.fromPartial(object.options) : void 0;
        return message;
      }
    };
    function createBaseEnumDescriptorProto() {
      return { name: "", value: [], options: void 0, reservedRange: [], reservedName: [], visibility: 0 };
    }
    exports2.EnumDescriptorProto = {
      encode(message, writer = new wire_1.BinaryWriter()) {
        if (message.name !== "") {
          writer.uint32(10).string(message.name);
        }
        for (const v of message.value) {
          exports2.EnumValueDescriptorProto.encode(v, writer.uint32(18).fork()).join();
        }
        if (message.options !== void 0) {
          exports2.EnumOptions.encode(message.options, writer.uint32(26).fork()).join();
        }
        for (const v of message.reservedRange) {
          exports2.EnumDescriptorProto_EnumReservedRange.encode(v, writer.uint32(34).fork()).join();
        }
        for (const v of message.reservedName) {
          writer.uint32(42).string(v);
        }
        if (message.visibility !== 0) {
          writer.uint32(48).int32(message.visibility);
        }
        if (message._unknownFields !== void 0) {
          for (const [key, values] of Object.entries(message._unknownFields)) {
            const tag = parseInt(key, 10);
            for (const value of values) {
              writer.uint32(tag).raw(value);
            }
          }
        }
        return writer;
      },
      decode(input, length) {
        const reader = input instanceof wire_1.BinaryReader ? input : new wire_1.BinaryReader(input);
        let end = length === void 0 ? reader.len : reader.pos + length;
        const message = Object.create(createBaseEnumDescriptorProto());
        while (reader.pos < end) {
          const tag = reader.uint32();
          switch (tag >>> 3) {
            case 1:
              if (tag !== 10) {
                break;
              }
              message.name = reader.string();
              continue;
            case 2:
              if (tag !== 18) {
                break;
              }
              message.value.push(exports2.EnumValueDescriptorProto.decode(reader, reader.uint32()));
              continue;
            case 3:
              if (tag !== 26) {
                break;
              }
              message.options = exports2.EnumOptions.decode(reader, reader.uint32());
              continue;
            case 4:
              if (tag !== 34) {
                break;
              }
              message.reservedRange.push(exports2.EnumDescriptorProto_EnumReservedRange.decode(reader, reader.uint32()));
              continue;
            case 5:
              if (tag !== 42) {
                break;
              }
              message.reservedName.push(reader.string());
              continue;
            case 6:
              if (tag !== 48) {
                break;
              }
              message.visibility = reader.int32();
              continue;
          }
          if ((tag & 7) === 4 || tag === 0) {
            break;
          }
          const buf = reader.skip(tag & 7);
          if (message._unknownFields === void 0) {
            message._unknownFields = {};
          }
          const list = message._unknownFields[tag];
          if (list === void 0) {
            message._unknownFields[tag] = [buf];
          } else {
            list.push(buf);
          }
        }
        return message;
      },
      fromJSON(object) {
        return {
          name: isSet(object.name) ? globalThis.String(object.name) : "",
          value: globalThis.Array.isArray(object?.value) ? object.value.map((e) => exports2.EnumValueDescriptorProto.fromJSON(e)) : [],
          options: isSet(object.options) ? exports2.EnumOptions.fromJSON(object.options) : void 0,
          reservedRange: globalThis.Array.isArray(object?.reservedRange) ? object.reservedRange.map((e) => exports2.EnumDescriptorProto_EnumReservedRange.fromJSON(e)) : [],
          reservedName: globalThis.Array.isArray(object?.reservedName) ? object.reservedName.map((e) => globalThis.String(e)) : [],
          visibility: isSet(object.visibility) ? symbolVisibilityFromJSON(object.visibility) : 0
        };
      },
      toJSON(message) {
        const obj = {};
        if (message.name !== "") {
          obj.name = message.name;
        }
        if (message.value?.length) {
          obj.value = message.value.map((e) => exports2.EnumValueDescriptorProto.toJSON(e));
        }
        if (message.options !== void 0) {
          obj.options = exports2.EnumOptions.toJSON(message.options);
        }
        if (message.reservedRange?.length) {
          obj.reservedRange = message.reservedRange.map((e) => exports2.EnumDescriptorProto_EnumReservedRange.toJSON(e));
        }
        if (message.reservedName?.length) {
          obj.reservedName = message.reservedName;
        }
        if (message.visibility !== 0) {
          obj.visibility = symbolVisibilityToJSON(message.visibility);
        }
        return obj;
      },
      create(base) {
        return exports2.EnumDescriptorProto.fromPartial(base ?? {});
      },
      fromPartial(object) {
        const message = Object.create(createBaseEnumDescriptorProto());
        message.name = object.name ?? "";
        message.value = object.value?.map((e) => exports2.EnumValueDescriptorProto.fromPartial(e)) || [];
        message.options = object.options !== void 0 && object.options !== null ? exports2.EnumOptions.fromPartial(object.options) : void 0;
        message.reservedRange = object.reservedRange?.map((e) => exports2.EnumDescriptorProto_EnumReservedRange.fromPartial(e)) || [];
        message.reservedName = object.reservedName?.map((e) => e) || [];
        message.visibility = object.visibility ?? 0;
        return message;
      }
    };
    function createBaseEnumDescriptorProto_EnumReservedRange() {
      return { start: 0, end: 0 };
    }
    exports2.EnumDescriptorProto_EnumReservedRange = {
      encode(message, writer = new wire_1.BinaryWriter()) {
        if (message.start !== 0) {
          writer.uint32(8).int32(message.start);
        }
        if (message.end !== 0) {
          writer.uint32(16).int32(message.end);
        }
        if (message._unknownFields !== void 0) {
          for (const [key, values] of Object.entries(message._unknownFields)) {
            const tag = parseInt(key, 10);
            for (const value of values) {
              writer.uint32(tag).raw(value);
            }
          }
        }
        return writer;
      },
      decode(input, length) {
        const reader = input instanceof wire_1.BinaryReader ? input : new wire_1.BinaryReader(input);
        let end = length === void 0 ? reader.len : reader.pos + length;
        const message = Object.create(createBaseEnumDescriptorProto_EnumReservedRange());
        while (reader.pos < end) {
          const tag = reader.uint32();
          switch (tag >>> 3) {
            case 1:
              if (tag !== 8) {
                break;
              }
              message.start = reader.int32();
              continue;
            case 2:
              if (tag !== 16) {
                break;
              }
              message.end = reader.int32();
              continue;
          }
          if ((tag & 7) === 4 || tag === 0) {
            break;
          }
          const buf = reader.skip(tag & 7);
          if (message._unknownFields === void 0) {
            message._unknownFields = {};
          }
          const list = message._unknownFields[tag];
          if (list === void 0) {
            message._unknownFields[tag] = [buf];
          } else {
            list.push(buf);
          }
        }
        return message;
      },
      fromJSON(object) {
        return {
          start: isSet(object.start) ? globalThis.Number(object.start) : 0,
          end: isSet(object.end) ? globalThis.Number(object.end) : 0
        };
      },
      toJSON(message) {
        const obj = {};
        if (message.start !== 0) {
          obj.start = Math.round(message.start);
        }
        if (message.end !== 0) {
          obj.end = Math.round(message.end);
        }
        return obj;
      },
      create(base) {
        return exports2.EnumDescriptorProto_EnumReservedRange.fromPartial(base ?? {});
      },
      fromPartial(object) {
        const message = Object.create(createBaseEnumDescriptorProto_EnumReservedRange());
        message.start = object.start ?? 0;
        message.end = object.end ?? 0;
        return message;
      }
    };
    function createBaseEnumValueDescriptorProto() {
      return { name: "", number: 0, options: void 0 };
    }
    exports2.EnumValueDescriptorProto = {
      encode(message, writer = new wire_1.BinaryWriter()) {
        if (message.name !== "") {
          writer.uint32(10).string(message.name);
        }
        if (message.number !== 0) {
          writer.uint32(16).int32(message.number);
        }
        if (message.options !== void 0) {
          exports2.EnumValueOptions.encode(message.options, writer.uint32(26).fork()).join();
        }
        if (message._unknownFields !== void 0) {
          for (const [key, values] of Object.entries(message._unknownFields)) {
            const tag = parseInt(key, 10);
            for (const value of values) {
              writer.uint32(tag).raw(value);
            }
          }
        }
        return writer;
      },
      decode(input, length) {
        const reader = input instanceof wire_1.BinaryReader ? input : new wire_1.BinaryReader(input);
        let end = length === void 0 ? reader.len : reader.pos + length;
        const message = Object.create(createBaseEnumValueDescriptorProto());
        while (reader.pos < end) {
          const tag = reader.uint32();
          switch (tag >>> 3) {
            case 1:
              if (tag !== 10) {
                break;
              }
              message.name = reader.string();
              continue;
            case 2:
              if (tag !== 16) {
                break;
              }
              message.number = reader.int32();
              continue;
            case 3:
              if (tag !== 26) {
                break;
              }
              message.options = exports2.EnumValueOptions.decode(reader, reader.uint32());
              continue;
          }
          if ((tag & 7) === 4 || tag === 0) {
            break;
          }
          const buf = reader.skip(tag & 7);
          if (message._unknownFields === void 0) {
            message._unknownFields = {};
          }
          const list = message._unknownFields[tag];
          if (list === void 0) {
            message._unknownFields[tag] = [buf];
          } else {
            list.push(buf);
          }
        }
        return message;
      },
      fromJSON(object) {
        return {
          name: isSet(object.name) ? globalThis.String(object.name) : "",
          number: isSet(object.number) ? globalThis.Number(object.number) : 0,
          options: isSet(object.options) ? exports2.EnumValueOptions.fromJSON(object.options) : void 0
        };
      },
      toJSON(message) {
        const obj = {};
        if (message.name !== "") {
          obj.name = message.name;
        }
        if (message.number !== 0) {
          obj.number = Math.round(message.number);
        }
        if (message.options !== void 0) {
          obj.options = exports2.EnumValueOptions.toJSON(message.options);
        }
        return obj;
      },
      create(base) {
        return exports2.EnumValueDescriptorProto.fromPartial(base ?? {});
      },
      fromPartial(object) {
        const message = Object.create(createBaseEnumValueDescriptorProto());
        message.name = object.name ?? "";
        message.number = object.number ?? 0;
        message.options = object.options !== void 0 && object.options !== null ? exports2.EnumValueOptions.fromPartial(object.options) : void 0;
        return message;
      }
    };
    function createBaseServiceDescriptorProto() {
      return { name: "", method: [], options: void 0 };
    }
    exports2.ServiceDescriptorProto = {
      encode(message, writer = new wire_1.BinaryWriter()) {
        if (message.name !== "") {
          writer.uint32(10).string(message.name);
        }
        for (const v of message.method) {
          exports2.MethodDescriptorProto.encode(v, writer.uint32(18).fork()).join();
        }
        if (message.options !== void 0) {
          exports2.ServiceOptions.encode(message.options, writer.uint32(26).fork()).join();
        }
        if (message._unknownFields !== void 0) {
          for (const [key, values] of Object.entries(message._unknownFields)) {
            const tag = parseInt(key, 10);
            for (const value of values) {
              writer.uint32(tag).raw(value);
            }
          }
        }
        return writer;
      },
      decode(input, length) {
        const reader = input instanceof wire_1.BinaryReader ? input : new wire_1.BinaryReader(input);
        let end = length === void 0 ? reader.len : reader.pos + length;
        const message = Object.create(createBaseServiceDescriptorProto());
        while (reader.pos < end) {
          const tag = reader.uint32();
          switch (tag >>> 3) {
            case 1:
              if (tag !== 10) {
                break;
              }
              message.name = reader.string();
              continue;
            case 2:
              if (tag !== 18) {
                break;
              }
              message.method.push(exports2.MethodDescriptorProto.decode(reader, reader.uint32()));
              continue;
            case 3:
              if (tag !== 26) {
                break;
              }
              message.options = exports2.ServiceOptions.decode(reader, reader.uint32());
              continue;
          }
          if ((tag & 7) === 4 || tag === 0) {
            break;
          }
          const buf = reader.skip(tag & 7);
          if (message._unknownFields === void 0) {
            message._unknownFields = {};
          }
          const list = message._unknownFields[tag];
          if (list === void 0) {
            message._unknownFields[tag] = [buf];
          } else {
            list.push(buf);
          }
        }
        return message;
      },
      fromJSON(object) {
        return {
          name: isSet(object.name) ? globalThis.String(object.name) : "",
          method: globalThis.Array.isArray(object?.method) ? object.method.map((e) => exports2.MethodDescriptorProto.fromJSON(e)) : [],
          options: isSet(object.options) ? exports2.ServiceOptions.fromJSON(object.options) : void 0
        };
      },
      toJSON(message) {
        const obj = {};
        if (message.name !== "") {
          obj.name = message.name;
        }
        if (message.method?.length) {
          obj.method = message.method.map((e) => exports2.MethodDescriptorProto.toJSON(e));
        }
        if (message.options !== void 0) {
          obj.options = exports2.ServiceOptions.toJSON(message.options);
        }
        return obj;
      },
      create(base) {
        return exports2.ServiceDescriptorProto.fromPartial(base ?? {});
      },
      fromPartial(object) {
        const message = Object.create(createBaseServiceDescriptorProto());
        message.name = object.name ?? "";
        message.method = object.method?.map((e) => exports2.MethodDescriptorProto.fromPartial(e)) || [];
        message.options = object.options !== void 0 && object.options !== null ? exports2.ServiceOptions.fromPartial(object.options) : void 0;
        return message;
      }
    };
    function createBaseMethodDescriptorProto() {
      return {
        name: "",
        inputType: "",
        outputType: "",
        options: void 0,
        clientStreaming: false,
        serverStreaming: false
      };
    }
    exports2.MethodDescriptorProto = {
      encode(message, writer = new wire_1.BinaryWriter()) {
        if (message.name !== "") {
          writer.uint32(10).string(message.name);
        }
        if (message.inputType !== "") {
          writer.uint32(18).string(message.inputType);
        }
        if (message.outputType !== "") {
          writer.uint32(26).string(message.outputType);
        }
        if (message.options !== void 0) {
          exports2.MethodOptions.encode(message.options, writer.uint32(34).fork()).join();
        }
        if (message.clientStreaming !== false) {
          writer.uint32(40).bool(message.clientStreaming);
        }
        if (message.serverStreaming !== false) {
          writer.uint32(48).bool(message.serverStreaming);
        }
        if (message._unknownFields !== void 0) {
          for (const [key, values] of Object.entries(message._unknownFields)) {
            const tag = parseInt(key, 10);
            for (const value of values) {
              writer.uint32(tag).raw(value);
            }
          }
        }
        return writer;
      },
      decode(input, length) {
        const reader = input instanceof wire_1.BinaryReader ? input : new wire_1.BinaryReader(input);
        let end = length === void 0 ? reader.len : reader.pos + length;
        const message = Object.create(createBaseMethodDescriptorProto());
        while (reader.pos < end) {
          const tag = reader.uint32();
          switch (tag >>> 3) {
            case 1:
              if (tag !== 10) {
                break;
              }
              message.name = reader.string();
              continue;
            case 2:
              if (tag !== 18) {
                break;
              }
              message.inputType = reader.string();
              continue;
            case 3:
              if (tag !== 26) {
                break;
              }
              message.outputType = reader.string();
              continue;
            case 4:
              if (tag !== 34) {
                break;
              }
              message.options = exports2.MethodOptions.decode(reader, reader.uint32());
              continue;
            case 5:
              if (tag !== 40) {
                break;
              }
              message.clientStreaming = reader.bool();
              continue;
            case 6:
              if (tag !== 48) {
                break;
              }
              message.serverStreaming = reader.bool();
              continue;
          }
          if ((tag & 7) === 4 || tag === 0) {
            break;
          }
          const buf = reader.skip(tag & 7);
          if (message._unknownFields === void 0) {
            message._unknownFields = {};
          }
          const list = message._unknownFields[tag];
          if (list === void 0) {
            message._unknownFields[tag] = [buf];
          } else {
            list.push(buf);
          }
        }
        return message;
      },
      fromJSON(object) {
        return {
          name: isSet(object.name) ? globalThis.String(object.name) : "",
          inputType: isSet(object.inputType) ? globalThis.String(object.inputType) : "",
          outputType: isSet(object.outputType) ? globalThis.String(object.outputType) : "",
          options: isSet(object.options) ? exports2.MethodOptions.fromJSON(object.options) : void 0,
          clientStreaming: isSet(object.clientStreaming) ? globalThis.Boolean(object.clientStreaming) : false,
          serverStreaming: isSet(object.serverStreaming) ? globalThis.Boolean(object.serverStreaming) : false
        };
      },
      toJSON(message) {
        const obj = {};
        if (message.name !== "") {
          obj.name = message.name;
        }
        if (message.inputType !== "") {
          obj.inputType = message.inputType;
        }
        if (message.outputType !== "") {
          obj.outputType = message.outputType;
        }
        if (message.options !== void 0) {
          obj.options = exports2.MethodOptions.toJSON(message.options);
        }
        if (message.clientStreaming !== false) {
          obj.clientStreaming = message.clientStreaming;
        }
        if (message.serverStreaming !== false) {
          obj.serverStreaming = message.serverStreaming;
        }
        return obj;
      },
      create(base) {
        return exports2.MethodDescriptorProto.fromPartial(base ?? {});
      },
      fromPartial(object) {
        const message = Object.create(createBaseMethodDescriptorProto());
        message.name = object.name ?? "";
        message.inputType = object.inputType ?? "";
        message.outputType = object.outputType ?? "";
        message.options = object.options !== void 0 && object.options !== null ? exports2.MethodOptions.fromPartial(object.options) : void 0;
        message.clientStreaming = object.clientStreaming ?? false;
        message.serverStreaming = object.serverStreaming ?? false;
        return message;
      }
    };
    function createBaseFileOptions() {
      return {
        javaPackage: "",
        javaOuterClassname: "",
        javaMultipleFiles: false,
        javaGenerateEqualsAndHash: false,
        javaStringCheckUtf8: false,
        optimizeFor: 1,
        goPackage: "",
        ccGenericServices: false,
        javaGenericServices: false,
        pyGenericServices: false,
        deprecated: false,
        ccEnableArenas: true,
        objcClassPrefix: "",
        csharpNamespace: "",
        swiftPrefix: "",
        phpClassPrefix: "",
        phpNamespace: "",
        phpMetadataNamespace: "",
        rubyPackage: "",
        features: void 0,
        uninterpretedOption: []
      };
    }
    exports2.FileOptions = {
      encode(message, writer = new wire_1.BinaryWriter()) {
        if (message.javaPackage !== "") {
          writer.uint32(10).string(message.javaPackage);
        }
        if (message.javaOuterClassname !== "") {
          writer.uint32(66).string(message.javaOuterClassname);
        }
        if (message.javaMultipleFiles !== false) {
          writer.uint32(80).bool(message.javaMultipleFiles);
        }
        if (message.javaGenerateEqualsAndHash !== false) {
          writer.uint32(160).bool(message.javaGenerateEqualsAndHash);
        }
        if (message.javaStringCheckUtf8 !== false) {
          writer.uint32(216).bool(message.javaStringCheckUtf8);
        }
        if (message.optimizeFor !== 1) {
          writer.uint32(72).int32(message.optimizeFor);
        }
        if (message.goPackage !== "") {
          writer.uint32(90).string(message.goPackage);
        }
        if (message.ccGenericServices !== false) {
          writer.uint32(128).bool(message.ccGenericServices);
        }
        if (message.javaGenericServices !== false) {
          writer.uint32(136).bool(message.javaGenericServices);
        }
        if (message.pyGenericServices !== false) {
          writer.uint32(144).bool(message.pyGenericServices);
        }
        if (message.deprecated !== false) {
          writer.uint32(184).bool(message.deprecated);
        }
        if (message.ccEnableArenas !== true) {
          writer.uint32(248).bool(message.ccEnableArenas);
        }
        if (message.objcClassPrefix !== "") {
          writer.uint32(290).string(message.objcClassPrefix);
        }
        if (message.csharpNamespace !== "") {
          writer.uint32(298).string(message.csharpNamespace);
        }
        if (message.swiftPrefix !== "") {
          writer.uint32(314).string(message.swiftPrefix);
        }
        if (message.phpClassPrefix !== "") {
          writer.uint32(322).string(message.phpClassPrefix);
        }
        if (message.phpNamespace !== "") {
          writer.uint32(330).string(message.phpNamespace);
        }
        if (message.phpMetadataNamespace !== "") {
          writer.uint32(354).string(message.phpMetadataNamespace);
        }
        if (message.rubyPackage !== "") {
          writer.uint32(362).string(message.rubyPackage);
        }
        if (message.features !== void 0) {
          exports2.FeatureSet.encode(message.features, writer.uint32(402).fork()).join();
        }
        for (const v of message.uninterpretedOption) {
          exports2.UninterpretedOption.encode(v, writer.uint32(7994).fork()).join();
        }
        if (message._unknownFields !== void 0) {
          for (const [key, values] of Object.entries(message._unknownFields)) {
            const tag = parseInt(key, 10);
            for (const value of values) {
              writer.uint32(tag).raw(value);
            }
          }
        }
        return writer;
      },
      setExtension(message, extension, value) {
        const encoded = extension.encode(value);
        if (message._unknownFields !== void 0) {
          delete message._unknownFields[extension.tag];
          if (extension.singularTag !== void 0) {
            delete message._unknownFields[extension.singularTag];
          }
        }
        if (encoded.length !== 0) {
          if (message._unknownFields === void 0) {
            message._unknownFields = {};
          }
          message._unknownFields[extension.tag] = encoded;
        }
      },
      decode(input, length) {
        const reader = input instanceof wire_1.BinaryReader ? input : new wire_1.BinaryReader(input);
        let end = length === void 0 ? reader.len : reader.pos + length;
        const message = Object.create(createBaseFileOptions());
        while (reader.pos < end) {
          const tag = reader.uint32();
          switch (tag >>> 3) {
            case 1:
              if (tag !== 10) {
                break;
              }
              message.javaPackage = reader.string();
              continue;
            case 8:
              if (tag !== 66) {
                break;
              }
              message.javaOuterClassname = reader.string();
              continue;
            case 10:
              if (tag !== 80) {
                break;
              }
              message.javaMultipleFiles = reader.bool();
              continue;
            case 20:
              if (tag !== 160) {
                break;
              }
              message.javaGenerateEqualsAndHash = reader.bool();
              continue;
            case 27:
              if (tag !== 216) {
                break;
              }
              message.javaStringCheckUtf8 = reader.bool();
              continue;
            case 9:
              if (tag !== 72) {
                break;
              }
              message.optimizeFor = reader.int32();
              continue;
            case 11:
              if (tag !== 90) {
                break;
              }
              message.goPackage = reader.string();
              continue;
            case 16:
              if (tag !== 128) {
                break;
              }
              message.ccGenericServices = reader.bool();
              continue;
            case 17:
              if (tag !== 136) {
                break;
              }
              message.javaGenericServices = reader.bool();
              continue;
            case 18:
              if (tag !== 144) {
                break;
              }
              message.pyGenericServices = reader.bool();
              continue;
            case 23:
              if (tag !== 184) {
                break;
              }
              message.deprecated = reader.bool();
              continue;
            case 31:
              if (tag !== 248) {
                break;
              }
              message.ccEnableArenas = reader.bool();
              continue;
            case 36:
              if (tag !== 290) {
                break;
              }
              message.objcClassPrefix = reader.string();
              continue;
            case 37:
              if (tag !== 298) {
                break;
              }
              message.csharpNamespace = reader.string();
              continue;
            case 39:
              if (tag !== 314) {
                break;
              }
              message.swiftPrefix = reader.string();
              continue;
            case 40:
              if (tag !== 322) {
                break;
              }
              message.phpClassPrefix = reader.string();
              continue;
            case 41:
              if (tag !== 330) {
                break;
              }
              message.phpNamespace = reader.string();
              continue;
            case 44:
              if (tag !== 354) {
                break;
              }
              message.phpMetadataNamespace = reader.string();
              continue;
            case 45:
              if (tag !== 362) {
                break;
              }
              message.rubyPackage = reader.string();
              continue;
            case 50:
              if (tag !== 402) {
                break;
              }
              message.features = exports2.FeatureSet.decode(reader, reader.uint32());
              continue;
            case 999:
              if (tag !== 7994) {
                break;
              }
              message.uninterpretedOption.push(exports2.UninterpretedOption.decode(reader, reader.uint32()));
              continue;
          }
          if ((tag & 7) === 4 || tag === 0) {
            break;
          }
          const buf = reader.skip(tag & 7);
          if (message._unknownFields === void 0) {
            message._unknownFields = {};
          }
          const list = message._unknownFields[tag];
          if (list === void 0) {
            message._unknownFields[tag] = [buf];
          } else {
            list.push(buf);
          }
        }
        return message;
      },
      getExtension(message, extension) {
        let results = void 0;
        if (message._unknownFields === void 0) {
          return void 0;
        }
        let list = message._unknownFields[extension.tag];
        if (list !== void 0) {
          results = extension.decode(extension.tag, list);
        }
        if (extension.singularTag === void 0) {
          return results;
        }
        list = message._unknownFields[extension.singularTag];
        if (list !== void 0) {
          const results2 = extension.decode(extension.singularTag, list);
          if (results !== void 0 && results.length !== 0) {
            results = results.concat(results2);
          } else {
            results = results2;
          }
        }
        return results;
      },
      fromJSON(object) {
        return {
          javaPackage: isSet(object.javaPackage) ? globalThis.String(object.javaPackage) : "",
          javaOuterClassname: isSet(object.javaOuterClassname) ? globalThis.String(object.javaOuterClassname) : "",
          javaMultipleFiles: isSet(object.javaMultipleFiles) ? globalThis.Boolean(object.javaMultipleFiles) : false,
          javaGenerateEqualsAndHash: isSet(object.javaGenerateEqualsAndHash) ? globalThis.Boolean(object.javaGenerateEqualsAndHash) : false,
          javaStringCheckUtf8: isSet(object.javaStringCheckUtf8) ? globalThis.Boolean(object.javaStringCheckUtf8) : false,
          optimizeFor: isSet(object.optimizeFor) ? fileOptions_OptimizeModeFromJSON(object.optimizeFor) : 1,
          goPackage: isSet(object.goPackage) ? globalThis.String(object.goPackage) : "",
          ccGenericServices: isSet(object.ccGenericServices) ? globalThis.Boolean(object.ccGenericServices) : false,
          javaGenericServices: isSet(object.javaGenericServices) ? globalThis.Boolean(object.javaGenericServices) : false,
          pyGenericServices: isSet(object.pyGenericServices) ? globalThis.Boolean(object.pyGenericServices) : false,
          deprecated: isSet(object.deprecated) ? globalThis.Boolean(object.deprecated) : false,
          ccEnableArenas: isSet(object.ccEnableArenas) ? globalThis.Boolean(object.ccEnableArenas) : true,
          objcClassPrefix: isSet(object.objcClassPrefix) ? globalThis.String(object.objcClassPrefix) : "",
          csharpNamespace: isSet(object.csharpNamespace) ? globalThis.String(object.csharpNamespace) : "",
          swiftPrefix: isSet(object.swiftPrefix) ? globalThis.String(object.swiftPrefix) : "",
          phpClassPrefix: isSet(object.phpClassPrefix) ? globalThis.String(object.phpClassPrefix) : "",
          phpNamespace: isSet(object.phpNamespace) ? globalThis.String(object.phpNamespace) : "",
          phpMetadataNamespace: isSet(object.phpMetadataNamespace) ? globalThis.String(object.phpMetadataNamespace) : "",
          rubyPackage: isSet(object.rubyPackage) ? globalThis.String(object.rubyPackage) : "",
          features: isSet(object.features) ? exports2.FeatureSet.fromJSON(object.features) : void 0,
          uninterpretedOption: globalThis.Array.isArray(object?.uninterpretedOption) ? object.uninterpretedOption.map((e) => exports2.UninterpretedOption.fromJSON(e)) : []
        };
      },
      toJSON(message) {
        const obj = {};
        if (message.javaPackage !== "") {
          obj.javaPackage = message.javaPackage;
        }
        if (message.javaOuterClassname !== "") {
          obj.javaOuterClassname = message.javaOuterClassname;
        }
        if (message.javaMultipleFiles !== false) {
          obj.javaMultipleFiles = message.javaMultipleFiles;
        }
        if (message.javaGenerateEqualsAndHash !== false) {
          obj.javaGenerateEqualsAndHash = message.javaGenerateEqualsAndHash;
        }
        if (message.javaStringCheckUtf8 !== false) {
          obj.javaStringCheckUtf8 = message.javaStringCheckUtf8;
        }
        if (message.optimizeFor !== 1) {
          obj.optimizeFor = fileOptions_OptimizeModeToJSON(message.optimizeFor);
        }
        if (message.goPackage !== "") {
          obj.goPackage = message.goPackage;
        }
        if (message.ccGenericServices !== false) {
          obj.ccGenericServices = message.ccGenericServices;
        }
        if (message.javaGenericServices !== false) {
          obj.javaGenericServices = message.javaGenericServices;
        }
        if (message.pyGenericServices !== false) {
          obj.pyGenericServices = message.pyGenericServices;
        }
        if (message.deprecated !== false) {
          obj.deprecated = message.deprecated;
        }
        if (message.ccEnableArenas !== true) {
          obj.ccEnableArenas = message.ccEnableArenas;
        }
        if (message.objcClassPrefix !== "") {
          obj.objcClassPrefix = message.objcClassPrefix;
        }
        if (message.csharpNamespace !== "") {
          obj.csharpNamespace = message.csharpNamespace;
        }
        if (message.swiftPrefix !== "") {
          obj.swiftPrefix = message.swiftPrefix;
        }
        if (message.phpClassPrefix !== "") {
          obj.phpClassPrefix = message.phpClassPrefix;
        }
        if (message.phpNamespace !== "") {
          obj.phpNamespace = message.phpNamespace;
        }
        if (message.phpMetadataNamespace !== "") {
          obj.phpMetadataNamespace = message.phpMetadataNamespace;
        }
        if (message.rubyPackage !== "") {
          obj.rubyPackage = message.rubyPackage;
        }
        if (message.features !== void 0) {
          obj.features = exports2.FeatureSet.toJSON(message.features);
        }
        if (message.uninterpretedOption?.length) {
          obj.uninterpretedOption = message.uninterpretedOption.map((e) => exports2.UninterpretedOption.toJSON(e));
        }
        return obj;
      },
      create(base) {
        return exports2.FileOptions.fromPartial(base ?? {});
      },
      fromPartial(object) {
        const message = Object.create(createBaseFileOptions());
        message.javaPackage = object.javaPackage ?? "";
        message.javaOuterClassname = object.javaOuterClassname ?? "";
        message.javaMultipleFiles = object.javaMultipleFiles ?? false;
        message.javaGenerateEqualsAndHash = object.javaGenerateEqualsAndHash ?? false;
        message.javaStringCheckUtf8 = object.javaStringCheckUtf8 ?? false;
        message.optimizeFor = object.optimizeFor ?? 1;
        message.goPackage = object.goPackage ?? "";
        message.ccGenericServices = object.ccGenericServices ?? false;
        message.javaGenericServices = object.javaGenericServices ?? false;
        message.pyGenericServices = object.pyGenericServices ?? false;
        message.deprecated = object.deprecated ?? false;
        message.ccEnableArenas = object.ccEnableArenas ?? true;
        message.objcClassPrefix = object.objcClassPrefix ?? "";
        message.csharpNamespace = object.csharpNamespace ?? "";
        message.swiftPrefix = object.swiftPrefix ?? "";
        message.phpClassPrefix = object.phpClassPrefix ?? "";
        message.phpNamespace = object.phpNamespace ?? "";
        message.phpMetadataNamespace = object.phpMetadataNamespace ?? "";
        message.rubyPackage = object.rubyPackage ?? "";
        message.features = object.features !== void 0 && object.features !== null ? exports2.FeatureSet.fromPartial(object.features) : void 0;
        message.uninterpretedOption = object.uninterpretedOption?.map((e) => exports2.UninterpretedOption.fromPartial(e)) || [];
        return message;
      }
    };
    function createBaseMessageOptions() {
      return {
        messageSetWireFormat: false,
        noStandardDescriptorAccessor: false,
        deprecated: false,
        mapEntry: false,
        deprecatedLegacyJsonFieldConflicts: false,
        features: void 0,
        uninterpretedOption: []
      };
    }
    exports2.MessageOptions = {
      encode(message, writer = new wire_1.BinaryWriter()) {
        if (message.messageSetWireFormat !== false) {
          writer.uint32(8).bool(message.messageSetWireFormat);
        }
        if (message.noStandardDescriptorAccessor !== false) {
          writer.uint32(16).bool(message.noStandardDescriptorAccessor);
        }
        if (message.deprecated !== false) {
          writer.uint32(24).bool(message.deprecated);
        }
        if (message.mapEntry !== false) {
          writer.uint32(56).bool(message.mapEntry);
        }
        if (message.deprecatedLegacyJsonFieldConflicts !== false) {
          writer.uint32(88).bool(message.deprecatedLegacyJsonFieldConflicts);
        }
        if (message.features !== void 0) {
          exports2.FeatureSet.encode(message.features, writer.uint32(98).fork()).join();
        }
        for (const v of message.uninterpretedOption) {
          exports2.UninterpretedOption.encode(v, writer.uint32(7994).fork()).join();
        }
        if (message._unknownFields !== void 0) {
          for (const [key, values] of Object.entries(message._unknownFields)) {
            const tag = parseInt(key, 10);
            for (const value of values) {
              writer.uint32(tag).raw(value);
            }
          }
        }
        return writer;
      },
      setExtension(message, extension, value) {
        const encoded = extension.encode(value);
        if (message._unknownFields !== void 0) {
          delete message._unknownFields[extension.tag];
          if (extension.singularTag !== void 0) {
            delete message._unknownFields[extension.singularTag];
          }
        }
        if (encoded.length !== 0) {
          if (message._unknownFields === void 0) {
            message._unknownFields = {};
          }
          message._unknownFields[extension.tag] = encoded;
        }
      },
      decode(input, length) {
        const reader = input instanceof wire_1.BinaryReader ? input : new wire_1.BinaryReader(input);
        let end = length === void 0 ? reader.len : reader.pos + length;
        const message = Object.create(createBaseMessageOptions());
        while (reader.pos < end) {
          const tag = reader.uint32();
          switch (tag >>> 3) {
            case 1:
              if (tag !== 8) {
                break;
              }
              message.messageSetWireFormat = reader.bool();
              continue;
            case 2:
              if (tag !== 16) {
                break;
              }
              message.noStandardDescriptorAccessor = reader.bool();
              continue;
            case 3:
              if (tag !== 24) {
                break;
              }
              message.deprecated = reader.bool();
              continue;
            case 7:
              if (tag !== 56) {
                break;
              }
              message.mapEntry = reader.bool();
              continue;
            case 11:
              if (tag !== 88) {
                break;
              }
              message.deprecatedLegacyJsonFieldConflicts = reader.bool();
              continue;
            case 12:
              if (tag !== 98) {
                break;
              }
              message.features = exports2.FeatureSet.decode(reader, reader.uint32());
              continue;
            case 999:
              if (tag !== 7994) {
                break;
              }
              message.uninterpretedOption.push(exports2.UninterpretedOption.decode(reader, reader.uint32()));
              continue;
          }
          if ((tag & 7) === 4 || tag === 0) {
            break;
          }
          const buf = reader.skip(tag & 7);
          if (message._unknownFields === void 0) {
            message._unknownFields = {};
          }
          const list = message._unknownFields[tag];
          if (list === void 0) {
            message._unknownFields[tag] = [buf];
          } else {
            list.push(buf);
          }
        }
        return message;
      },
      getExtension(message, extension) {
        let results = void 0;
        if (message._unknownFields === void 0) {
          return void 0;
        }
        let list = message._unknownFields[extension.tag];
        if (list !== void 0) {
          results = extension.decode(extension.tag, list);
        }
        if (extension.singularTag === void 0) {
          return results;
        }
        list = message._unknownFields[extension.singularTag];
        if (list !== void 0) {
          const results2 = extension.decode(extension.singularTag, list);
          if (results !== void 0 && results.length !== 0) {
            results = results.concat(results2);
          } else {
            results = results2;
          }
        }
        return results;
      },
      fromJSON(object) {
        return {
          messageSetWireFormat: isSet(object.messageSetWireFormat) ? globalThis.Boolean(object.messageSetWireFormat) : false,
          noStandardDescriptorAccessor: isSet(object.noStandardDescriptorAccessor) ? globalThis.Boolean(object.noStandardDescriptorAccessor) : false,
          deprecated: isSet(object.deprecated) ? globalThis.Boolean(object.deprecated) : false,
          mapEntry: isSet(object.mapEntry) ? globalThis.Boolean(object.mapEntry) : false,
          deprecatedLegacyJsonFieldConflicts: isSet(object.deprecatedLegacyJsonFieldConflicts) ? globalThis.Boolean(object.deprecatedLegacyJsonFieldConflicts) : false,
          features: isSet(object.features) ? exports2.FeatureSet.fromJSON(object.features) : void 0,
          uninterpretedOption: globalThis.Array.isArray(object?.uninterpretedOption) ? object.uninterpretedOption.map((e) => exports2.UninterpretedOption.fromJSON(e)) : []
        };
      },
      toJSON(message) {
        const obj = {};
        if (message.messageSetWireFormat !== false) {
          obj.messageSetWireFormat = message.messageSetWireFormat;
        }
        if (message.noStandardDescriptorAccessor !== false) {
          obj.noStandardDescriptorAccessor = message.noStandardDescriptorAccessor;
        }
        if (message.deprecated !== false) {
          obj.deprecated = message.deprecated;
        }
        if (message.mapEntry !== false) {
          obj.mapEntry = message.mapEntry;
        }
        if (message.deprecatedLegacyJsonFieldConflicts !== false) {
          obj.deprecatedLegacyJsonFieldConflicts = message.deprecatedLegacyJsonFieldConflicts;
        }
        if (message.features !== void 0) {
          obj.features = exports2.FeatureSet.toJSON(message.features);
        }
        if (message.uninterpretedOption?.length) {
          obj.uninterpretedOption = message.uninterpretedOption.map((e) => exports2.UninterpretedOption.toJSON(e));
        }
        return obj;
      },
      create(base) {
        return exports2.MessageOptions.fromPartial(base ?? {});
      },
      fromPartial(object) {
        const message = Object.create(createBaseMessageOptions());
        message.messageSetWireFormat = object.messageSetWireFormat ?? false;
        message.noStandardDescriptorAccessor = object.noStandardDescriptorAccessor ?? false;
        message.deprecated = object.deprecated ?? false;
        message.mapEntry = object.mapEntry ?? false;
        message.deprecatedLegacyJsonFieldConflicts = object.deprecatedLegacyJsonFieldConflicts ?? false;
        message.features = object.features !== void 0 && object.features !== null ? exports2.FeatureSet.fromPartial(object.features) : void 0;
        message.uninterpretedOption = object.uninterpretedOption?.map((e) => exports2.UninterpretedOption.fromPartial(e)) || [];
        return message;
      }
    };
    function createBaseFieldOptions() {
      return {
        ctype: 0,
        packed: false,
        jstype: 0,
        lazy: false,
        unverifiedLazy: false,
        deprecated: false,
        weak: false,
        debugRedact: false,
        retention: 0,
        targets: [],
        editionDefaults: [],
        features: void 0,
        featureSupport: void 0,
        uninterpretedOption: []
      };
    }
    exports2.FieldOptions = {
      encode(message, writer = new wire_1.BinaryWriter()) {
        if (message.ctype !== 0) {
          writer.uint32(8).int32(message.ctype);
        }
        if (message.packed !== false) {
          writer.uint32(16).bool(message.packed);
        }
        if (message.jstype !== 0) {
          writer.uint32(48).int32(message.jstype);
        }
        if (message.lazy !== false) {
          writer.uint32(40).bool(message.lazy);
        }
        if (message.unverifiedLazy !== false) {
          writer.uint32(120).bool(message.unverifiedLazy);
        }
        if (message.deprecated !== false) {
          writer.uint32(24).bool(message.deprecated);
        }
        if (message.weak !== false) {
          writer.uint32(80).bool(message.weak);
        }
        if (message.debugRedact !== false) {
          writer.uint32(128).bool(message.debugRedact);
        }
        if (message.retention !== 0) {
          writer.uint32(136).int32(message.retention);
        }
        writer.uint32(154).fork();
        for (const v of message.targets) {
          writer.int32(v);
        }
        writer.join();
        for (const v of message.editionDefaults) {
          exports2.FieldOptions_EditionDefault.encode(v, writer.uint32(162).fork()).join();
        }
        if (message.features !== void 0) {
          exports2.FeatureSet.encode(message.features, writer.uint32(170).fork()).join();
        }
        if (message.featureSupport !== void 0) {
          exports2.FieldOptions_FeatureSupport.encode(message.featureSupport, writer.uint32(178).fork()).join();
        }
        for (const v of message.uninterpretedOption) {
          exports2.UninterpretedOption.encode(v, writer.uint32(7994).fork()).join();
        }
        if (message._unknownFields !== void 0) {
          for (const [key, values] of Object.entries(message._unknownFields)) {
            const tag = parseInt(key, 10);
            for (const value of values) {
              writer.uint32(tag).raw(value);
            }
          }
        }
        return writer;
      },
      setExtension(message, extension, value) {
        const encoded = extension.encode(value);
        if (message._unknownFields !== void 0) {
          delete message._unknownFields[extension.tag];
          if (extension.singularTag !== void 0) {
            delete message._unknownFields[extension.singularTag];
          }
        }
        if (encoded.length !== 0) {
          if (message._unknownFields === void 0) {
            message._unknownFields = {};
          }
          message._unknownFields[extension.tag] = encoded;
        }
      },
      decode(input, length) {
        const reader = input instanceof wire_1.BinaryReader ? input : new wire_1.BinaryReader(input);
        let end = length === void 0 ? reader.len : reader.pos + length;
        const message = Object.create(createBaseFieldOptions());
        while (reader.pos < end) {
          const tag = reader.uint32();
          switch (tag >>> 3) {
            case 1:
              if (tag !== 8) {
                break;
              }
              message.ctype = reader.int32();
              continue;
            case 2:
              if (tag !== 16) {
                break;
              }
              message.packed = reader.bool();
              continue;
            case 6:
              if (tag !== 48) {
                break;
              }
              message.jstype = reader.int32();
              continue;
            case 5:
              if (tag !== 40) {
                break;
              }
              message.lazy = reader.bool();
              continue;
            case 15:
              if (tag !== 120) {
                break;
              }
              message.unverifiedLazy = reader.bool();
              continue;
            case 3:
              if (tag !== 24) {
                break;
              }
              message.deprecated = reader.bool();
              continue;
            case 10:
              if (tag !== 80) {
                break;
              }
              message.weak = reader.bool();
              continue;
            case 16:
              if (tag !== 128) {
                break;
              }
              message.debugRedact = reader.bool();
              continue;
            case 17:
              if (tag !== 136) {
                break;
              }
              message.retention = reader.int32();
              continue;
            case 19:
              if (tag === 152) {
                message.targets.push(reader.int32());
                continue;
              }
              if (tag === 154) {
                const end2 = reader.uint32() + reader.pos;
                while (reader.pos < end2) {
                  message.targets.push(reader.int32());
                }
                continue;
              }
              break;
            case 20:
              if (tag !== 162) {
                break;
              }
              message.editionDefaults.push(exports2.FieldOptions_EditionDefault.decode(reader, reader.uint32()));
              continue;
            case 21:
              if (tag !== 170) {
                break;
              }
              message.features = exports2.FeatureSet.decode(reader, reader.uint32());
              continue;
            case 22:
              if (tag !== 178) {
                break;
              }
              message.featureSupport = exports2.FieldOptions_FeatureSupport.decode(reader, reader.uint32());
              continue;
            case 999:
              if (tag !== 7994) {
                break;
              }
              message.uninterpretedOption.push(exports2.UninterpretedOption.decode(reader, reader.uint32()));
              continue;
          }
          if ((tag & 7) === 4 || tag === 0) {
            break;
          }
          const buf = reader.skip(tag & 7);
          if (message._unknownFields === void 0) {
            message._unknownFields = {};
          }
          const list = message._unknownFields[tag];
          if (list === void 0) {
            message._unknownFields[tag] = [buf];
          } else {
            list.push(buf);
          }
        }
        return message;
      },
      getExtension(message, extension) {
        let results = void 0;
        if (message._unknownFields === void 0) {
          return void 0;
        }
        let list = message._unknownFields[extension.tag];
        if (list !== void 0) {
          results = extension.decode(extension.tag, list);
        }
        if (extension.singularTag === void 0) {
          return results;
        }
        list = message._unknownFields[extension.singularTag];
        if (list !== void 0) {
          const results2 = extension.decode(extension.singularTag, list);
          if (results !== void 0 && results.length !== 0) {
            results = results.concat(results2);
          } else {
            results = results2;
          }
        }
        return results;
      },
      fromJSON(object) {
        return {
          ctype: isSet(object.ctype) ? fieldOptions_CTypeFromJSON(object.ctype) : 0,
          packed: isSet(object.packed) ? globalThis.Boolean(object.packed) : false,
          jstype: isSet(object.jstype) ? fieldOptions_JSTypeFromJSON(object.jstype) : 0,
          lazy: isSet(object.lazy) ? globalThis.Boolean(object.lazy) : false,
          unverifiedLazy: isSet(object.unverifiedLazy) ? globalThis.Boolean(object.unverifiedLazy) : false,
          deprecated: isSet(object.deprecated) ? globalThis.Boolean(object.deprecated) : false,
          weak: isSet(object.weak) ? globalThis.Boolean(object.weak) : false,
          debugRedact: isSet(object.debugRedact) ? globalThis.Boolean(object.debugRedact) : false,
          retention: isSet(object.retention) ? fieldOptions_OptionRetentionFromJSON(object.retention) : 0,
          targets: globalThis.Array.isArray(object?.targets) ? object.targets.map((e) => fieldOptions_OptionTargetTypeFromJSON(e)) : [],
          editionDefaults: globalThis.Array.isArray(object?.editionDefaults) ? object.editionDefaults.map((e) => exports2.FieldOptions_EditionDefault.fromJSON(e)) : [],
          features: isSet(object.features) ? exports2.FeatureSet.fromJSON(object.features) : void 0,
          featureSupport: isSet(object.featureSupport) ? exports2.FieldOptions_FeatureSupport.fromJSON(object.featureSupport) : void 0,
          uninterpretedOption: globalThis.Array.isArray(object?.uninterpretedOption) ? object.uninterpretedOption.map((e) => exports2.UninterpretedOption.fromJSON(e)) : []
        };
      },
      toJSON(message) {
        const obj = {};
        if (message.ctype !== 0) {
          obj.ctype = fieldOptions_CTypeToJSON(message.ctype);
        }
        if (message.packed !== false) {
          obj.packed = message.packed;
        }
        if (message.jstype !== 0) {
          obj.jstype = fieldOptions_JSTypeToJSON(message.jstype);
        }
        if (message.lazy !== false) {
          obj.lazy = message.lazy;
        }
        if (message.unverifiedLazy !== false) {
          obj.unverifiedLazy = message.unverifiedLazy;
        }
        if (message.deprecated !== false) {
          obj.deprecated = message.deprecated;
        }
        if (message.weak !== false) {
          obj.weak = message.weak;
        }
        if (message.debugRedact !== false) {
          obj.debugRedact = message.debugRedact;
        }
        if (message.retention !== 0) {
          obj.retention = fieldOptions_OptionRetentionToJSON(message.retention);
        }
        if (message.targets?.length) {
          obj.targets = message.targets.map((e) => fieldOptions_OptionTargetTypeToJSON(e));
        }
        if (message.editionDefaults?.length) {
          obj.editionDefaults = message.editionDefaults.map((e) => exports2.FieldOptions_EditionDefault.toJSON(e));
        }
        if (message.features !== void 0) {
          obj.features = exports2.FeatureSet.toJSON(message.features);
        }
        if (message.featureSupport !== void 0) {
          obj.featureSupport = exports2.FieldOptions_FeatureSupport.toJSON(message.featureSupport);
        }
        if (message.uninterpretedOption?.length) {
          obj.uninterpretedOption = message.uninterpretedOption.map((e) => exports2.UninterpretedOption.toJSON(e));
        }
        return obj;
      },
      create(base) {
        return exports2.FieldOptions.fromPartial(base ?? {});
      },
      fromPartial(object) {
        const message = Object.create(createBaseFieldOptions());
        message.ctype = object.ctype ?? 0;
        message.packed = object.packed ?? false;
        message.jstype = object.jstype ?? 0;
        message.lazy = object.lazy ?? false;
        message.unverifiedLazy = object.unverifiedLazy ?? false;
        message.deprecated = object.deprecated ?? false;
        message.weak = object.weak ?? false;
        message.debugRedact = object.debugRedact ?? false;
        message.retention = object.retention ?? 0;
        message.targets = object.targets?.map((e) => e) || [];
        message.editionDefaults = object.editionDefaults?.map((e) => exports2.FieldOptions_EditionDefault.fromPartial(e)) || [];
        message.features = object.features !== void 0 && object.features !== null ? exports2.FeatureSet.fromPartial(object.features) : void 0;
        message.featureSupport = object.featureSupport !== void 0 && object.featureSupport !== null ? exports2.FieldOptions_FeatureSupport.fromPartial(object.featureSupport) : void 0;
        message.uninterpretedOption = object.uninterpretedOption?.map((e) => exports2.UninterpretedOption.fromPartial(e)) || [];
        return message;
      }
    };
    function createBaseFieldOptions_EditionDefault() {
      return { edition: 0, value: "" };
    }
    exports2.FieldOptions_EditionDefault = {
      encode(message, writer = new wire_1.BinaryWriter()) {
        if (message.edition !== 0) {
          writer.uint32(24).int32(message.edition);
        }
        if (message.value !== "") {
          writer.uint32(18).string(message.value);
        }
        if (message._unknownFields !== void 0) {
          for (const [key, values] of Object.entries(message._unknownFields)) {
            const tag = parseInt(key, 10);
            for (const value of values) {
              writer.uint32(tag).raw(value);
            }
          }
        }
        return writer;
      },
      decode(input, length) {
        const reader = input instanceof wire_1.BinaryReader ? input : new wire_1.BinaryReader(input);
        let end = length === void 0 ? reader.len : reader.pos + length;
        const message = Object.create(createBaseFieldOptions_EditionDefault());
        while (reader.pos < end) {
          const tag = reader.uint32();
          switch (tag >>> 3) {
            case 3:
              if (tag !== 24) {
                break;
              }
              message.edition = reader.int32();
              continue;
            case 2:
              if (tag !== 18) {
                break;
              }
              message.value = reader.string();
              continue;
          }
          if ((tag & 7) === 4 || tag === 0) {
            break;
          }
          const buf = reader.skip(tag & 7);
          if (message._unknownFields === void 0) {
            message._unknownFields = {};
          }
          const list = message._unknownFields[tag];
          if (list === void 0) {
            message._unknownFields[tag] = [buf];
          } else {
            list.push(buf);
          }
        }
        return message;
      },
      fromJSON(object) {
        return {
          edition: isSet(object.edition) ? editionFromJSON(object.edition) : 0,
          value: isSet(object.value) ? globalThis.String(object.value) : ""
        };
      },
      toJSON(message) {
        const obj = {};
        if (message.edition !== 0) {
          obj.edition = editionToJSON(message.edition);
        }
        if (message.value !== "") {
          obj.value = message.value;
        }
        return obj;
      },
      create(base) {
        return exports2.FieldOptions_EditionDefault.fromPartial(base ?? {});
      },
      fromPartial(object) {
        const message = Object.create(createBaseFieldOptions_EditionDefault());
        message.edition = object.edition ?? 0;
        message.value = object.value ?? "";
        return message;
      }
    };
    function createBaseFieldOptions_FeatureSupport() {
      return { editionIntroduced: 0, editionDeprecated: 0, deprecationWarning: "", editionRemoved: 0 };
    }
    exports2.FieldOptions_FeatureSupport = {
      encode(message, writer = new wire_1.BinaryWriter()) {
        if (message.editionIntroduced !== 0) {
          writer.uint32(8).int32(message.editionIntroduced);
        }
        if (message.editionDeprecated !== 0) {
          writer.uint32(16).int32(message.editionDeprecated);
        }
        if (message.deprecationWarning !== "") {
          writer.uint32(26).string(message.deprecationWarning);
        }
        if (message.editionRemoved !== 0) {
          writer.uint32(32).int32(message.editionRemoved);
        }
        if (message._unknownFields !== void 0) {
          for (const [key, values] of Object.entries(message._unknownFields)) {
            const tag = parseInt(key, 10);
            for (const value of values) {
              writer.uint32(tag).raw(value);
            }
          }
        }
        return writer;
      },
      decode(input, length) {
        const reader = input instanceof wire_1.BinaryReader ? input : new wire_1.BinaryReader(input);
        let end = length === void 0 ? reader.len : reader.pos + length;
        const message = Object.create(createBaseFieldOptions_FeatureSupport());
        while (reader.pos < end) {
          const tag = reader.uint32();
          switch (tag >>> 3) {
            case 1:
              if (tag !== 8) {
                break;
              }
              message.editionIntroduced = reader.int32();
              continue;
            case 2:
              if (tag !== 16) {
                break;
              }
              message.editionDeprecated = reader.int32();
              continue;
            case 3:
              if (tag !== 26) {
                break;
              }
              message.deprecationWarning = reader.string();
              continue;
            case 4:
              if (tag !== 32) {
                break;
              }
              message.editionRemoved = reader.int32();
              continue;
          }
          if ((tag & 7) === 4 || tag === 0) {
            break;
          }
          const buf = reader.skip(tag & 7);
          if (message._unknownFields === void 0) {
            message._unknownFields = {};
          }
          const list = message._unknownFields[tag];
          if (list === void 0) {
            message._unknownFields[tag] = [buf];
          } else {
            list.push(buf);
          }
        }
        return message;
      },
      fromJSON(object) {
        return {
          editionIntroduced: isSet(object.editionIntroduced) ? editionFromJSON(object.editionIntroduced) : 0,
          editionDeprecated: isSet(object.editionDeprecated) ? editionFromJSON(object.editionDeprecated) : 0,
          deprecationWarning: isSet(object.deprecationWarning) ? globalThis.String(object.deprecationWarning) : "",
          editionRemoved: isSet(object.editionRemoved) ? editionFromJSON(object.editionRemoved) : 0
        };
      },
      toJSON(message) {
        const obj = {};
        if (message.editionIntroduced !== 0) {
          obj.editionIntroduced = editionToJSON(message.editionIntroduced);
        }
        if (message.editionDeprecated !== 0) {
          obj.editionDeprecated = editionToJSON(message.editionDeprecated);
        }
        if (message.deprecationWarning !== "") {
          obj.deprecationWarning = message.deprecationWarning;
        }
        if (message.editionRemoved !== 0) {
          obj.editionRemoved = editionToJSON(message.editionRemoved);
        }
        return obj;
      },
      create(base) {
        return exports2.FieldOptions_FeatureSupport.fromPartial(base ?? {});
      },
      fromPartial(object) {
        const message = Object.create(createBaseFieldOptions_FeatureSupport());
        message.editionIntroduced = object.editionIntroduced ?? 0;
        message.editionDeprecated = object.editionDeprecated ?? 0;
        message.deprecationWarning = object.deprecationWarning ?? "";
        message.editionRemoved = object.editionRemoved ?? 0;
        return message;
      }
    };
    function createBaseOneofOptions() {
      return { features: void 0, uninterpretedOption: [] };
    }
    exports2.OneofOptions = {
      encode(message, writer = new wire_1.BinaryWriter()) {
        if (message.features !== void 0) {
          exports2.FeatureSet.encode(message.features, writer.uint32(10).fork()).join();
        }
        for (const v of message.uninterpretedOption) {
          exports2.UninterpretedOption.encode(v, writer.uint32(7994).fork()).join();
        }
        if (message._unknownFields !== void 0) {
          for (const [key, values] of Object.entries(message._unknownFields)) {
            const tag = parseInt(key, 10);
            for (const value of values) {
              writer.uint32(tag).raw(value);
            }
          }
        }
        return writer;
      },
      setExtension(message, extension, value) {
        const encoded = extension.encode(value);
        if (message._unknownFields !== void 0) {
          delete message._unknownFields[extension.tag];
          if (extension.singularTag !== void 0) {
            delete message._unknownFields[extension.singularTag];
          }
        }
        if (encoded.length !== 0) {
          if (message._unknownFields === void 0) {
            message._unknownFields = {};
          }
          message._unknownFields[extension.tag] = encoded;
        }
      },
      decode(input, length) {
        const reader = input instanceof wire_1.BinaryReader ? input : new wire_1.BinaryReader(input);
        let end = length === void 0 ? reader.len : reader.pos + length;
        const message = Object.create(createBaseOneofOptions());
        while (reader.pos < end) {
          const tag = reader.uint32();
          switch (tag >>> 3) {
            case 1:
              if (tag !== 10) {
                break;
              }
              message.features = exports2.FeatureSet.decode(reader, reader.uint32());
              continue;
            case 999:
              if (tag !== 7994) {
                break;
              }
              message.uninterpretedOption.push(exports2.UninterpretedOption.decode(reader, reader.uint32()));
              continue;
          }
          if ((tag & 7) === 4 || tag === 0) {
            break;
          }
          const buf = reader.skip(tag & 7);
          if (message._unknownFields === void 0) {
            message._unknownFields = {};
          }
          const list = message._unknownFields[tag];
          if (list === void 0) {
            message._unknownFields[tag] = [buf];
          } else {
            list.push(buf);
          }
        }
        return message;
      },
      getExtension(message, extension) {
        let results = void 0;
        if (message._unknownFields === void 0) {
          return void 0;
        }
        let list = message._unknownFields[extension.tag];
        if (list !== void 0) {
          results = extension.decode(extension.tag, list);
        }
        if (extension.singularTag === void 0) {
          return results;
        }
        list = message._unknownFields[extension.singularTag];
        if (list !== void 0) {
          const results2 = extension.decode(extension.singularTag, list);
          if (results !== void 0 && results.length !== 0) {
            results = results.concat(results2);
          } else {
            results = results2;
          }
        }
        return results;
      },
      fromJSON(object) {
        return {
          features: isSet(object.features) ? exports2.FeatureSet.fromJSON(object.features) : void 0,
          uninterpretedOption: globalThis.Array.isArray(object?.uninterpretedOption) ? object.uninterpretedOption.map((e) => exports2.UninterpretedOption.fromJSON(e)) : []
        };
      },
      toJSON(message) {
        const obj = {};
        if (message.features !== void 0) {
          obj.features = exports2.FeatureSet.toJSON(message.features);
        }
        if (message.uninterpretedOption?.length) {
          obj.uninterpretedOption = message.uninterpretedOption.map((e) => exports2.UninterpretedOption.toJSON(e));
        }
        return obj;
      },
      create(base) {
        return exports2.OneofOptions.fromPartial(base ?? {});
      },
      fromPartial(object) {
        const message = Object.create(createBaseOneofOptions());
        message.features = object.features !== void 0 && object.features !== null ? exports2.FeatureSet.fromPartial(object.features) : void 0;
        message.uninterpretedOption = object.uninterpretedOption?.map((e) => exports2.UninterpretedOption.fromPartial(e)) || [];
        return message;
      }
    };
    function createBaseEnumOptions() {
      return {
        allowAlias: false,
        deprecated: false,
        deprecatedLegacyJsonFieldConflicts: false,
        features: void 0,
        uninterpretedOption: []
      };
    }
    exports2.EnumOptions = {
      encode(message, writer = new wire_1.BinaryWriter()) {
        if (message.allowAlias !== false) {
          writer.uint32(16).bool(message.allowAlias);
        }
        if (message.deprecated !== false) {
          writer.uint32(24).bool(message.deprecated);
        }
        if (message.deprecatedLegacyJsonFieldConflicts !== false) {
          writer.uint32(48).bool(message.deprecatedLegacyJsonFieldConflicts);
        }
        if (message.features !== void 0) {
          exports2.FeatureSet.encode(message.features, writer.uint32(58).fork()).join();
        }
        for (const v of message.uninterpretedOption) {
          exports2.UninterpretedOption.encode(v, writer.uint32(7994).fork()).join();
        }
        if (message._unknownFields !== void 0) {
          for (const [key, values] of Object.entries(message._unknownFields)) {
            const tag = parseInt(key, 10);
            for (const value of values) {
              writer.uint32(tag).raw(value);
            }
          }
        }
        return writer;
      },
      setExtension(message, extension, value) {
        const encoded = extension.encode(value);
        if (message._unknownFields !== void 0) {
          delete message._unknownFields[extension.tag];
          if (extension.singularTag !== void 0) {
            delete message._unknownFields[extension.singularTag];
          }
        }
        if (encoded.length !== 0) {
          if (message._unknownFields === void 0) {
            message._unknownFields = {};
          }
          message._unknownFields[extension.tag] = encoded;
        }
      },
      decode(input, length) {
        const reader = input instanceof wire_1.BinaryReader ? input : new wire_1.BinaryReader(input);
        let end = length === void 0 ? reader.len : reader.pos + length;
        const message = Object.create(createBaseEnumOptions());
        while (reader.pos < end) {
          const tag = reader.uint32();
          switch (tag >>> 3) {
            case 2:
              if (tag !== 16) {
                break;
              }
              message.allowAlias = reader.bool();
              continue;
            case 3:
              if (tag !== 24) {
                break;
              }
              message.deprecated = reader.bool();
              continue;
            case 6:
              if (tag !== 48) {
                break;
              }
              message.deprecatedLegacyJsonFieldConflicts = reader.bool();
              continue;
            case 7:
              if (tag !== 58) {
                break;
              }
              message.features = exports2.FeatureSet.decode(reader, reader.uint32());
              continue;
            case 999:
              if (tag !== 7994) {
                break;
              }
              message.uninterpretedOption.push(exports2.UninterpretedOption.decode(reader, reader.uint32()));
              continue;
          }
          if ((tag & 7) === 4 || tag === 0) {
            break;
          }
          const buf = reader.skip(tag & 7);
          if (message._unknownFields === void 0) {
            message._unknownFields = {};
          }
          const list = message._unknownFields[tag];
          if (list === void 0) {
            message._unknownFields[tag] = [buf];
          } else {
            list.push(buf);
          }
        }
        return message;
      },
      getExtension(message, extension) {
        let results = void 0;
        if (message._unknownFields === void 0) {
          return void 0;
        }
        let list = message._unknownFields[extension.tag];
        if (list !== void 0) {
          results = extension.decode(extension.tag, list);
        }
        if (extension.singularTag === void 0) {
          return results;
        }
        list = message._unknownFields[extension.singularTag];
        if (list !== void 0) {
          const results2 = extension.decode(extension.singularTag, list);
          if (results !== void 0 && results.length !== 0) {
            results = results.concat(results2);
          } else {
            results = results2;
          }
        }
        return results;
      },
      fromJSON(object) {
        return {
          allowAlias: isSet(object.allowAlias) ? globalThis.Boolean(object.allowAlias) : false,
          deprecated: isSet(object.deprecated) ? globalThis.Boolean(object.deprecated) : false,
          deprecatedLegacyJsonFieldConflicts: isSet(object.deprecatedLegacyJsonFieldConflicts) ? globalThis.Boolean(object.deprecatedLegacyJsonFieldConflicts) : false,
          features: isSet(object.features) ? exports2.FeatureSet.fromJSON(object.features) : void 0,
          uninterpretedOption: globalThis.Array.isArray(object?.uninterpretedOption) ? object.uninterpretedOption.map((e) => exports2.UninterpretedOption.fromJSON(e)) : []
        };
      },
      toJSON(message) {
        const obj = {};
        if (message.allowAlias !== false) {
          obj.allowAlias = message.allowAlias;
        }
        if (message.deprecated !== false) {
          obj.deprecated = message.deprecated;
        }
        if (message.deprecatedLegacyJsonFieldConflicts !== false) {
          obj.deprecatedLegacyJsonFieldConflicts = message.deprecatedLegacyJsonFieldConflicts;
        }
        if (message.features !== void 0) {
          obj.features = exports2.FeatureSet.toJSON(message.features);
        }
        if (message.uninterpretedOption?.length) {
          obj.uninterpretedOption = message.uninterpretedOption.map((e) => exports2.UninterpretedOption.toJSON(e));
        }
        return obj;
      },
      create(base) {
        return exports2.EnumOptions.fromPartial(base ?? {});
      },
      fromPartial(object) {
        const message = Object.create(createBaseEnumOptions());
        message.allowAlias = object.allowAlias ?? false;
        message.deprecated = object.deprecated ?? false;
        message.deprecatedLegacyJsonFieldConflicts = object.deprecatedLegacyJsonFieldConflicts ?? false;
        message.features = object.features !== void 0 && object.features !== null ? exports2.FeatureSet.fromPartial(object.features) : void 0;
        message.uninterpretedOption = object.uninterpretedOption?.map((e) => exports2.UninterpretedOption.fromPartial(e)) || [];
        return message;
      }
    };
    function createBaseEnumValueOptions() {
      return {
        deprecated: false,
        features: void 0,
        debugRedact: false,
        featureSupport: void 0,
        uninterpretedOption: []
      };
    }
    exports2.EnumValueOptions = {
      encode(message, writer = new wire_1.BinaryWriter()) {
        if (message.deprecated !== false) {
          writer.uint32(8).bool(message.deprecated);
        }
        if (message.features !== void 0) {
          exports2.FeatureSet.encode(message.features, writer.uint32(18).fork()).join();
        }
        if (message.debugRedact !== false) {
          writer.uint32(24).bool(message.debugRedact);
        }
        if (message.featureSupport !== void 0) {
          exports2.FieldOptions_FeatureSupport.encode(message.featureSupport, writer.uint32(34).fork()).join();
        }
        for (const v of message.uninterpretedOption) {
          exports2.UninterpretedOption.encode(v, writer.uint32(7994).fork()).join();
        }
        if (message._unknownFields !== void 0) {
          for (const [key, values] of Object.entries(message._unknownFields)) {
            const tag = parseInt(key, 10);
            for (const value of values) {
              writer.uint32(tag).raw(value);
            }
          }
        }
        return writer;
      },
      setExtension(message, extension, value) {
        const encoded = extension.encode(value);
        if (message._unknownFields !== void 0) {
          delete message._unknownFields[extension.tag];
          if (extension.singularTag !== void 0) {
            delete message._unknownFields[extension.singularTag];
          }
        }
        if (encoded.length !== 0) {
          if (message._unknownFields === void 0) {
            message._unknownFields = {};
          }
          message._unknownFields[extension.tag] = encoded;
        }
      },
      decode(input, length) {
        const reader = input instanceof wire_1.BinaryReader ? input : new wire_1.BinaryReader(input);
        let end = length === void 0 ? reader.len : reader.pos + length;
        const message = Object.create(createBaseEnumValueOptions());
        while (reader.pos < end) {
          const tag = reader.uint32();
          switch (tag >>> 3) {
            case 1:
              if (tag !== 8) {
                break;
              }
              message.deprecated = reader.bool();
              continue;
            case 2:
              if (tag !== 18) {
                break;
              }
              message.features = exports2.FeatureSet.decode(reader, reader.uint32());
              continue;
            case 3:
              if (tag !== 24) {
                break;
              }
              message.debugRedact = reader.bool();
              continue;
            case 4:
              if (tag !== 34) {
                break;
              }
              message.featureSupport = exports2.FieldOptions_FeatureSupport.decode(reader, reader.uint32());
              continue;
            case 999:
              if (tag !== 7994) {
                break;
              }
              message.uninterpretedOption.push(exports2.UninterpretedOption.decode(reader, reader.uint32()));
              continue;
          }
          if ((tag & 7) === 4 || tag === 0) {
            break;
          }
          const buf = reader.skip(tag & 7);
          if (message._unknownFields === void 0) {
            message._unknownFields = {};
          }
          const list = message._unknownFields[tag];
          if (list === void 0) {
            message._unknownFields[tag] = [buf];
          } else {
            list.push(buf);
          }
        }
        return message;
      },
      getExtension(message, extension) {
        let results = void 0;
        if (message._unknownFields === void 0) {
          return void 0;
        }
        let list = message._unknownFields[extension.tag];
        if (list !== void 0) {
          results = extension.decode(extension.tag, list);
        }
        if (extension.singularTag === void 0) {
          return results;
        }
        list = message._unknownFields[extension.singularTag];
        if (list !== void 0) {
          const results2 = extension.decode(extension.singularTag, list);
          if (results !== void 0 && results.length !== 0) {
            results = results.concat(results2);
          } else {
            results = results2;
          }
        }
        return results;
      },
      fromJSON(object) {
        return {
          deprecated: isSet(object.deprecated) ? globalThis.Boolean(object.deprecated) : false,
          features: isSet(object.features) ? exports2.FeatureSet.fromJSON(object.features) : void 0,
          debugRedact: isSet(object.debugRedact) ? globalThis.Boolean(object.debugRedact) : false,
          featureSupport: isSet(object.featureSupport) ? exports2.FieldOptions_FeatureSupport.fromJSON(object.featureSupport) : void 0,
          uninterpretedOption: globalThis.Array.isArray(object?.uninterpretedOption) ? object.uninterpretedOption.map((e) => exports2.UninterpretedOption.fromJSON(e)) : []
        };
      },
      toJSON(message) {
        const obj = {};
        if (message.deprecated !== false) {
          obj.deprecated = message.deprecated;
        }
        if (message.features !== void 0) {
          obj.features = exports2.FeatureSet.toJSON(message.features);
        }
        if (message.debugRedact !== false) {
          obj.debugRedact = message.debugRedact;
        }
        if (message.featureSupport !== void 0) {
          obj.featureSupport = exports2.FieldOptions_FeatureSupport.toJSON(message.featureSupport);
        }
        if (message.uninterpretedOption?.length) {
          obj.uninterpretedOption = message.uninterpretedOption.map((e) => exports2.UninterpretedOption.toJSON(e));
        }
        return obj;
      },
      create(base) {
        return exports2.EnumValueOptions.fromPartial(base ?? {});
      },
      fromPartial(object) {
        const message = Object.create(createBaseEnumValueOptions());
        message.deprecated = object.deprecated ?? false;
        message.features = object.features !== void 0 && object.features !== null ? exports2.FeatureSet.fromPartial(object.features) : void 0;
        message.debugRedact = object.debugRedact ?? false;
        message.featureSupport = object.featureSupport !== void 0 && object.featureSupport !== null ? exports2.FieldOptions_FeatureSupport.fromPartial(object.featureSupport) : void 0;
        message.uninterpretedOption = object.uninterpretedOption?.map((e) => exports2.UninterpretedOption.fromPartial(e)) || [];
        return message;
      }
    };
    function createBaseServiceOptions() {
      return { features: void 0, deprecated: false, uninterpretedOption: [] };
    }
    exports2.ServiceOptions = {
      encode(message, writer = new wire_1.BinaryWriter()) {
        if (message.features !== void 0) {
          exports2.FeatureSet.encode(message.features, writer.uint32(274).fork()).join();
        }
        if (message.deprecated !== false) {
          writer.uint32(264).bool(message.deprecated);
        }
        for (const v of message.uninterpretedOption) {
          exports2.UninterpretedOption.encode(v, writer.uint32(7994).fork()).join();
        }
        if (message._unknownFields !== void 0) {
          for (const [key, values] of Object.entries(message._unknownFields)) {
            const tag = parseInt(key, 10);
            for (const value of values) {
              writer.uint32(tag).raw(value);
            }
          }
        }
        return writer;
      },
      setExtension(message, extension, value) {
        const encoded = extension.encode(value);
        if (message._unknownFields !== void 0) {
          delete message._unknownFields[extension.tag];
          if (extension.singularTag !== void 0) {
            delete message._unknownFields[extension.singularTag];
          }
        }
        if (encoded.length !== 0) {
          if (message._unknownFields === void 0) {
            message._unknownFields = {};
          }
          message._unknownFields[extension.tag] = encoded;
        }
      },
      decode(input, length) {
        const reader = input instanceof wire_1.BinaryReader ? input : new wire_1.BinaryReader(input);
        let end = length === void 0 ? reader.len : reader.pos + length;
        const message = Object.create(createBaseServiceOptions());
        while (reader.pos < end) {
          const tag = reader.uint32();
          switch (tag >>> 3) {
            case 34:
              if (tag !== 274) {
                break;
              }
              message.features = exports2.FeatureSet.decode(reader, reader.uint32());
              continue;
            case 33:
              if (tag !== 264) {
                break;
              }
              message.deprecated = reader.bool();
              continue;
            case 999:
              if (tag !== 7994) {
                break;
              }
              message.uninterpretedOption.push(exports2.UninterpretedOption.decode(reader, reader.uint32()));
              continue;
          }
          if ((tag & 7) === 4 || tag === 0) {
            break;
          }
          const buf = reader.skip(tag & 7);
          if (message._unknownFields === void 0) {
            message._unknownFields = {};
          }
          const list = message._unknownFields[tag];
          if (list === void 0) {
            message._unknownFields[tag] = [buf];
          } else {
            list.push(buf);
          }
        }
        return message;
      },
      getExtension(message, extension) {
        let results = void 0;
        if (message._unknownFields === void 0) {
          return void 0;
        }
        let list = message._unknownFields[extension.tag];
        if (list !== void 0) {
          results = extension.decode(extension.tag, list);
        }
        if (extension.singularTag === void 0) {
          return results;
        }
        list = message._unknownFields[extension.singularTag];
        if (list !== void 0) {
          const results2 = extension.decode(extension.singularTag, list);
          if (results !== void 0 && results.length !== 0) {
            results = results.concat(results2);
          } else {
            results = results2;
          }
        }
        return results;
      },
      fromJSON(object) {
        return {
          features: isSet(object.features) ? exports2.FeatureSet.fromJSON(object.features) : void 0,
          deprecated: isSet(object.deprecated) ? globalThis.Boolean(object.deprecated) : false,
          uninterpretedOption: globalThis.Array.isArray(object?.uninterpretedOption) ? object.uninterpretedOption.map((e) => exports2.UninterpretedOption.fromJSON(e)) : []
        };
      },
      toJSON(message) {
        const obj = {};
        if (message.features !== void 0) {
          obj.features = exports2.FeatureSet.toJSON(message.features);
        }
        if (message.deprecated !== false) {
          obj.deprecated = message.deprecated;
        }
        if (message.uninterpretedOption?.length) {
          obj.uninterpretedOption = message.uninterpretedOption.map((e) => exports2.UninterpretedOption.toJSON(e));
        }
        return obj;
      },
      create(base) {
        return exports2.ServiceOptions.fromPartial(base ?? {});
      },
      fromPartial(object) {
        const message = Object.create(createBaseServiceOptions());
        message.features = object.features !== void 0 && object.features !== null ? exports2.FeatureSet.fromPartial(object.features) : void 0;
        message.deprecated = object.deprecated ?? false;
        message.uninterpretedOption = object.uninterpretedOption?.map((e) => exports2.UninterpretedOption.fromPartial(e)) || [];
        return message;
      }
    };
    function createBaseMethodOptions() {
      return { deprecated: false, idempotencyLevel: 0, features: void 0, uninterpretedOption: [] };
    }
    exports2.MethodOptions = {
      encode(message, writer = new wire_1.BinaryWriter()) {
        if (message.deprecated !== false) {
          writer.uint32(264).bool(message.deprecated);
        }
        if (message.idempotencyLevel !== 0) {
          writer.uint32(272).int32(message.idempotencyLevel);
        }
        if (message.features !== void 0) {
          exports2.FeatureSet.encode(message.features, writer.uint32(282).fork()).join();
        }
        for (const v of message.uninterpretedOption) {
          exports2.UninterpretedOption.encode(v, writer.uint32(7994).fork()).join();
        }
        if (message._unknownFields !== void 0) {
          for (const [key, values] of Object.entries(message._unknownFields)) {
            const tag = parseInt(key, 10);
            for (const value of values) {
              writer.uint32(tag).raw(value);
            }
          }
        }
        return writer;
      },
      setExtension(message, extension, value) {
        const encoded = extension.encode(value);
        if (message._unknownFields !== void 0) {
          delete message._unknownFields[extension.tag];
          if (extension.singularTag !== void 0) {
            delete message._unknownFields[extension.singularTag];
          }
        }
        if (encoded.length !== 0) {
          if (message._unknownFields === void 0) {
            message._unknownFields = {};
          }
          message._unknownFields[extension.tag] = encoded;
        }
      },
      decode(input, length) {
        const reader = input instanceof wire_1.BinaryReader ? input : new wire_1.BinaryReader(input);
        let end = length === void 0 ? reader.len : reader.pos + length;
        const message = Object.create(createBaseMethodOptions());
        while (reader.pos < end) {
          const tag = reader.uint32();
          switch (tag >>> 3) {
            case 33:
              if (tag !== 264) {
                break;
              }
              message.deprecated = reader.bool();
              continue;
            case 34:
              if (tag !== 272) {
                break;
              }
              message.idempotencyLevel = reader.int32();
              continue;
            case 35:
              if (tag !== 282) {
                break;
              }
              message.features = exports2.FeatureSet.decode(reader, reader.uint32());
              continue;
            case 999:
              if (tag !== 7994) {
                break;
              }
              message.uninterpretedOption.push(exports2.UninterpretedOption.decode(reader, reader.uint32()));
              continue;
          }
          if ((tag & 7) === 4 || tag === 0) {
            break;
          }
          const buf = reader.skip(tag & 7);
          if (message._unknownFields === void 0) {
            message._unknownFields = {};
          }
          const list = message._unknownFields[tag];
          if (list === void 0) {
            message._unknownFields[tag] = [buf];
          } else {
            list.push(buf);
          }
        }
        return message;
      },
      getExtension(message, extension) {
        let results = void 0;
        if (message._unknownFields === void 0) {
          return void 0;
        }
        let list = message._unknownFields[extension.tag];
        if (list !== void 0) {
          results = extension.decode(extension.tag, list);
        }
        if (extension.singularTag === void 0) {
          return results;
        }
        list = message._unknownFields[extension.singularTag];
        if (list !== void 0) {
          const results2 = extension.decode(extension.singularTag, list);
          if (results !== void 0 && results.length !== 0) {
            results = results.concat(results2);
          } else {
            results = results2;
          }
        }
        return results;
      },
      fromJSON(object) {
        return {
          deprecated: isSet(object.deprecated) ? globalThis.Boolean(object.deprecated) : false,
          idempotencyLevel: isSet(object.idempotencyLevel) ? methodOptions_IdempotencyLevelFromJSON(object.idempotencyLevel) : 0,
          features: isSet(object.features) ? exports2.FeatureSet.fromJSON(object.features) : void 0,
          uninterpretedOption: globalThis.Array.isArray(object?.uninterpretedOption) ? object.uninterpretedOption.map((e) => exports2.UninterpretedOption.fromJSON(e)) : []
        };
      },
      toJSON(message) {
        const obj = {};
        if (message.deprecated !== false) {
          obj.deprecated = message.deprecated;
        }
        if (message.idempotencyLevel !== 0) {
          obj.idempotencyLevel = methodOptions_IdempotencyLevelToJSON(message.idempotencyLevel);
        }
        if (message.features !== void 0) {
          obj.features = exports2.FeatureSet.toJSON(message.features);
        }
        if (message.uninterpretedOption?.length) {
          obj.uninterpretedOption = message.uninterpretedOption.map((e) => exports2.UninterpretedOption.toJSON(e));
        }
        return obj;
      },
      create(base) {
        return exports2.MethodOptions.fromPartial(base ?? {});
      },
      fromPartial(object) {
        const message = Object.create(createBaseMethodOptions());
        message.deprecated = object.deprecated ?? false;
        message.idempotencyLevel = object.idempotencyLevel ?? 0;
        message.features = object.features !== void 0 && object.features !== null ? exports2.FeatureSet.fromPartial(object.features) : void 0;
        message.uninterpretedOption = object.uninterpretedOption?.map((e) => exports2.UninterpretedOption.fromPartial(e)) || [];
        return message;
      }
    };
    function createBaseUninterpretedOption() {
      return {
        name: [],
        identifierValue: "",
        positiveIntValue: 0,
        negativeIntValue: 0,
        doubleValue: 0,
        stringValue: new Uint8Array(0),
        aggregateValue: ""
      };
    }
    exports2.UninterpretedOption = {
      encode(message, writer = new wire_1.BinaryWriter()) {
        for (const v of message.name) {
          exports2.UninterpretedOption_NamePart.encode(v, writer.uint32(18).fork()).join();
        }
        if (message.identifierValue !== "") {
          writer.uint32(26).string(message.identifierValue);
        }
        if (message.positiveIntValue !== 0) {
          writer.uint32(32).uint64(message.positiveIntValue);
        }
        if (message.negativeIntValue !== 0) {
          writer.uint32(40).int64(message.negativeIntValue);
        }
        if (message.doubleValue !== 0) {
          writer.uint32(49).double(message.doubleValue);
        }
        if (message.stringValue.length !== 0) {
          writer.uint32(58).bytes(message.stringValue);
        }
        if (message.aggregateValue !== "") {
          writer.uint32(66).string(message.aggregateValue);
        }
        if (message._unknownFields !== void 0) {
          for (const [key, values] of Object.entries(message._unknownFields)) {
            const tag = parseInt(key, 10);
            for (const value of values) {
              writer.uint32(tag).raw(value);
            }
          }
        }
        return writer;
      },
      decode(input, length) {
        const reader = input instanceof wire_1.BinaryReader ? input : new wire_1.BinaryReader(input);
        let end = length === void 0 ? reader.len : reader.pos + length;
        const message = Object.create(createBaseUninterpretedOption());
        while (reader.pos < end) {
          const tag = reader.uint32();
          switch (tag >>> 3) {
            case 2:
              if (tag !== 18) {
                break;
              }
              message.name.push(exports2.UninterpretedOption_NamePart.decode(reader, reader.uint32()));
              continue;
            case 3:
              if (tag !== 26) {
                break;
              }
              message.identifierValue = reader.string();
              continue;
            case 4:
              if (tag !== 32) {
                break;
              }
              message.positiveIntValue = longToNumber(reader.uint64());
              continue;
            case 5:
              if (tag !== 40) {
                break;
              }
              message.negativeIntValue = longToNumber(reader.int64());
              continue;
            case 6:
              if (tag !== 49) {
                break;
              }
              message.doubleValue = reader.double();
              continue;
            case 7:
              if (tag !== 58) {
                break;
              }
              message.stringValue = reader.bytes();
              continue;
            case 8:
              if (tag !== 66) {
                break;
              }
              message.aggregateValue = reader.string();
              continue;
          }
          if ((tag & 7) === 4 || tag === 0) {
            break;
          }
          const buf = reader.skip(tag & 7);
          if (message._unknownFields === void 0) {
            message._unknownFields = {};
          }
          const list = message._unknownFields[tag];
          if (list === void 0) {
            message._unknownFields[tag] = [buf];
          } else {
            list.push(buf);
          }
        }
        return message;
      },
      fromJSON(object) {
        return {
          name: globalThis.Array.isArray(object?.name) ? object.name.map((e) => exports2.UninterpretedOption_NamePart.fromJSON(e)) : [],
          identifierValue: isSet(object.identifierValue) ? globalThis.String(object.identifierValue) : "",
          positiveIntValue: isSet(object.positiveIntValue) ? globalThis.Number(object.positiveIntValue) : 0,
          negativeIntValue: isSet(object.negativeIntValue) ? globalThis.Number(object.negativeIntValue) : 0,
          doubleValue: isSet(object.doubleValue) ? globalThis.Number(object.doubleValue) : 0,
          stringValue: isSet(object.stringValue) ? bytesFromBase64(object.stringValue) : new Uint8Array(0),
          aggregateValue: isSet(object.aggregateValue) ? globalThis.String(object.aggregateValue) : ""
        };
      },
      toJSON(message) {
        const obj = {};
        if (message.name?.length) {
          obj.name = message.name.map((e) => exports2.UninterpretedOption_NamePart.toJSON(e));
        }
        if (message.identifierValue !== "") {
          obj.identifierValue = message.identifierValue;
        }
        if (message.positiveIntValue !== 0) {
          obj.positiveIntValue = Math.round(message.positiveIntValue);
        }
        if (message.negativeIntValue !== 0) {
          obj.negativeIntValue = Math.round(message.negativeIntValue);
        }
        if (message.doubleValue !== 0) {
          obj.doubleValue = message.doubleValue;
        }
        if (message.stringValue.length !== 0) {
          obj.stringValue = base64FromBytes(message.stringValue);
        }
        if (message.aggregateValue !== "") {
          obj.aggregateValue = message.aggregateValue;
        }
        return obj;
      },
      create(base) {
        return exports2.UninterpretedOption.fromPartial(base ?? {});
      },
      fromPartial(object) {
        const message = Object.create(createBaseUninterpretedOption());
        message.name = object.name?.map((e) => exports2.UninterpretedOption_NamePart.fromPartial(e)) || [];
        message.identifierValue = object.identifierValue ?? "";
        message.positiveIntValue = object.positiveIntValue ?? 0;
        message.negativeIntValue = object.negativeIntValue ?? 0;
        message.doubleValue = object.doubleValue ?? 0;
        message.stringValue = object.stringValue ?? new Uint8Array(0);
        message.aggregateValue = object.aggregateValue ?? "";
        return message;
      }
    };
    function createBaseUninterpretedOption_NamePart() {
      return { namePart: "", isExtension: false };
    }
    exports2.UninterpretedOption_NamePart = {
      encode(message, writer = new wire_1.BinaryWriter()) {
        if (message.namePart !== "") {
          writer.uint32(10).string(message.namePart);
        }
        if (message.isExtension !== false) {
          writer.uint32(16).bool(message.isExtension);
        }
        if (message._unknownFields !== void 0) {
          for (const [key, values] of Object.entries(message._unknownFields)) {
            const tag = parseInt(key, 10);
            for (const value of values) {
              writer.uint32(tag).raw(value);
            }
          }
        }
        return writer;
      },
      decode(input, length) {
        const reader = input instanceof wire_1.BinaryReader ? input : new wire_1.BinaryReader(input);
        let end = length === void 0 ? reader.len : reader.pos + length;
        const message = Object.create(createBaseUninterpretedOption_NamePart());
        while (reader.pos < end) {
          const tag = reader.uint32();
          switch (tag >>> 3) {
            case 1:
              if (tag !== 10) {
                break;
              }
              message.namePart = reader.string();
              continue;
            case 2:
              if (tag !== 16) {
                break;
              }
              message.isExtension = reader.bool();
              continue;
          }
          if ((tag & 7) === 4 || tag === 0) {
            break;
          }
          const buf = reader.skip(tag & 7);
          if (message._unknownFields === void 0) {
            message._unknownFields = {};
          }
          const list = message._unknownFields[tag];
          if (list === void 0) {
            message._unknownFields[tag] = [buf];
          } else {
            list.push(buf);
          }
        }
        return message;
      },
      fromJSON(object) {
        return {
          namePart: isSet(object.namePart) ? globalThis.String(object.namePart) : "",
          isExtension: isSet(object.isExtension) ? globalThis.Boolean(object.isExtension) : false
        };
      },
      toJSON(message) {
        const obj = {};
        if (message.namePart !== "") {
          obj.namePart = message.namePart;
        }
        if (message.isExtension !== false) {
          obj.isExtension = message.isExtension;
        }
        return obj;
      },
      create(base) {
        return exports2.UninterpretedOption_NamePart.fromPartial(base ?? {});
      },
      fromPartial(object) {
        const message = Object.create(createBaseUninterpretedOption_NamePart());
        message.namePart = object.namePart ?? "";
        message.isExtension = object.isExtension ?? false;
        return message;
      }
    };
    function createBaseFeatureSet() {
      return {
        fieldPresence: 0,
        enumType: 0,
        repeatedFieldEncoding: 0,
        utf8Validation: 0,
        messageEncoding: 0,
        jsonFormat: 0,
        enforceNamingStyle: 0,
        defaultSymbolVisibility: 0
      };
    }
    exports2.FeatureSet = {
      encode(message, writer = new wire_1.BinaryWriter()) {
        if (message.fieldPresence !== 0) {
          writer.uint32(8).int32(message.fieldPresence);
        }
        if (message.enumType !== 0) {
          writer.uint32(16).int32(message.enumType);
        }
        if (message.repeatedFieldEncoding !== 0) {
          writer.uint32(24).int32(message.repeatedFieldEncoding);
        }
        if (message.utf8Validation !== 0) {
          writer.uint32(32).int32(message.utf8Validation);
        }
        if (message.messageEncoding !== 0) {
          writer.uint32(40).int32(message.messageEncoding);
        }
        if (message.jsonFormat !== 0) {
          writer.uint32(48).int32(message.jsonFormat);
        }
        if (message.enforceNamingStyle !== 0) {
          writer.uint32(56).int32(message.enforceNamingStyle);
        }
        if (message.defaultSymbolVisibility !== 0) {
          writer.uint32(64).int32(message.defaultSymbolVisibility);
        }
        if (message._unknownFields !== void 0) {
          for (const [key, values] of Object.entries(message._unknownFields)) {
            const tag = parseInt(key, 10);
            for (const value of values) {
              writer.uint32(tag).raw(value);
            }
          }
        }
        return writer;
      },
      setExtension(message, extension, value) {
        const encoded = extension.encode(value);
        if (message._unknownFields !== void 0) {
          delete message._unknownFields[extension.tag];
          if (extension.singularTag !== void 0) {
            delete message._unknownFields[extension.singularTag];
          }
        }
        if (encoded.length !== 0) {
          if (message._unknownFields === void 0) {
            message._unknownFields = {};
          }
          message._unknownFields[extension.tag] = encoded;
        }
      },
      decode(input, length) {
        const reader = input instanceof wire_1.BinaryReader ? input : new wire_1.BinaryReader(input);
        let end = length === void 0 ? reader.len : reader.pos + length;
        const message = Object.create(createBaseFeatureSet());
        while (reader.pos < end) {
          const tag = reader.uint32();
          switch (tag >>> 3) {
            case 1:
              if (tag !== 8) {
                break;
              }
              message.fieldPresence = reader.int32();
              continue;
            case 2:
              if (tag !== 16) {
                break;
              }
              message.enumType = reader.int32();
              continue;
            case 3:
              if (tag !== 24) {
                break;
              }
              message.repeatedFieldEncoding = reader.int32();
              continue;
            case 4:
              if (tag !== 32) {
                break;
              }
              message.utf8Validation = reader.int32();
              continue;
            case 5:
              if (tag !== 40) {
                break;
              }
              message.messageEncoding = reader.int32();
              continue;
            case 6:
              if (tag !== 48) {
                break;
              }
              message.jsonFormat = reader.int32();
              continue;
            case 7:
              if (tag !== 56) {
                break;
              }
              message.enforceNamingStyle = reader.int32();
              continue;
            case 8:
              if (tag !== 64) {
                break;
              }
              message.defaultSymbolVisibility = reader.int32();
              continue;
          }
          if ((tag & 7) === 4 || tag === 0) {
            break;
          }
          const buf = reader.skip(tag & 7);
          if (message._unknownFields === void 0) {
            message._unknownFields = {};
          }
          const list = message._unknownFields[tag];
          if (list === void 0) {
            message._unknownFields[tag] = [buf];
          } else {
            list.push(buf);
          }
        }
        return message;
      },
      getExtension(message, extension) {
        let results = void 0;
        if (message._unknownFields === void 0) {
          return void 0;
        }
        let list = message._unknownFields[extension.tag];
        if (list !== void 0) {
          results = extension.decode(extension.tag, list);
        }
        if (extension.singularTag === void 0) {
          return results;
        }
        list = message._unknownFields[extension.singularTag];
        if (list !== void 0) {
          const results2 = extension.decode(extension.singularTag, list);
          if (results !== void 0 && results.length !== 0) {
            results = results.concat(results2);
          } else {
            results = results2;
          }
        }
        return results;
      },
      fromJSON(object) {
        return {
          fieldPresence: isSet(object.fieldPresence) ? featureSet_FieldPresenceFromJSON(object.fieldPresence) : 0,
          enumType: isSet(object.enumType) ? featureSet_EnumTypeFromJSON(object.enumType) : 0,
          repeatedFieldEncoding: isSet(object.repeatedFieldEncoding) ? featureSet_RepeatedFieldEncodingFromJSON(object.repeatedFieldEncoding) : 0,
          utf8Validation: isSet(object.utf8Validation) ? featureSet_Utf8ValidationFromJSON(object.utf8Validation) : 0,
          messageEncoding: isSet(object.messageEncoding) ? featureSet_MessageEncodingFromJSON(object.messageEncoding) : 0,
          jsonFormat: isSet(object.jsonFormat) ? featureSet_JsonFormatFromJSON(object.jsonFormat) : 0,
          enforceNamingStyle: isSet(object.enforceNamingStyle) ? featureSet_EnforceNamingStyleFromJSON(object.enforceNamingStyle) : 0,
          defaultSymbolVisibility: isSet(object.defaultSymbolVisibility) ? featureSet_VisibilityFeature_DefaultSymbolVisibilityFromJSON(object.defaultSymbolVisibility) : 0
        };
      },
      toJSON(message) {
        const obj = {};
        if (message.fieldPresence !== 0) {
          obj.fieldPresence = featureSet_FieldPresenceToJSON(message.fieldPresence);
        }
        if (message.enumType !== 0) {
          obj.enumType = featureSet_EnumTypeToJSON(message.enumType);
        }
        if (message.repeatedFieldEncoding !== 0) {
          obj.repeatedFieldEncoding = featureSet_RepeatedFieldEncodingToJSON(message.repeatedFieldEncoding);
        }
        if (message.utf8Validation !== 0) {
          obj.utf8Validation = featureSet_Utf8ValidationToJSON(message.utf8Validation);
        }
        if (message.messageEncoding !== 0) {
          obj.messageEncoding = featureSet_MessageEncodingToJSON(message.messageEncoding);
        }
        if (message.jsonFormat !== 0) {
          obj.jsonFormat = featureSet_JsonFormatToJSON(message.jsonFormat);
        }
        if (message.enforceNamingStyle !== 0) {
          obj.enforceNamingStyle = featureSet_EnforceNamingStyleToJSON(message.enforceNamingStyle);
        }
        if (message.defaultSymbolVisibility !== 0) {
          obj.defaultSymbolVisibility = featureSet_VisibilityFeature_DefaultSymbolVisibilityToJSON(message.defaultSymbolVisibility);
        }
        return obj;
      },
      create(base) {
        return exports2.FeatureSet.fromPartial(base ?? {});
      },
      fromPartial(object) {
        const message = Object.create(createBaseFeatureSet());
        message.fieldPresence = object.fieldPresence ?? 0;
        message.enumType = object.enumType ?? 0;
        message.repeatedFieldEncoding = object.repeatedFieldEncoding ?? 0;
        message.utf8Validation = object.utf8Validation ?? 0;
        message.messageEncoding = object.messageEncoding ?? 0;
        message.jsonFormat = object.jsonFormat ?? 0;
        message.enforceNamingStyle = object.enforceNamingStyle ?? 0;
        message.defaultSymbolVisibility = object.defaultSymbolVisibility ?? 0;
        return message;
      }
    };
    function createBaseFeatureSet_VisibilityFeature() {
      return {};
    }
    exports2.FeatureSet_VisibilityFeature = {
      encode(message, writer = new wire_1.BinaryWriter()) {
        if (message._unknownFields !== void 0) {
          for (const [key, values] of Object.entries(message._unknownFields)) {
            const tag = parseInt(key, 10);
            for (const value of values) {
              writer.uint32(tag).raw(value);
            }
          }
        }
        return writer;
      },
      decode(input, length) {
        const reader = input instanceof wire_1.BinaryReader ? input : new wire_1.BinaryReader(input);
        let end = length === void 0 ? reader.len : reader.pos + length;
        const message = Object.create(createBaseFeatureSet_VisibilityFeature());
        while (reader.pos < end) {
          const tag = reader.uint32();
          switch (tag >>> 3) {
          }
          if ((tag & 7) === 4 || tag === 0) {
            break;
          }
          const buf = reader.skip(tag & 7);
          if (message._unknownFields === void 0) {
            message._unknownFields = {};
          }
          const list = message._unknownFields[tag];
          if (list === void 0) {
            message._unknownFields[tag] = [buf];
          } else {
            list.push(buf);
          }
        }
        return message;
      },
      fromJSON(_) {
        return {};
      },
      toJSON(_) {
        const obj = {};
        return obj;
      },
      create(base) {
        return exports2.FeatureSet_VisibilityFeature.fromPartial(base ?? {});
      },
      fromPartial(_) {
        const message = Object.create(createBaseFeatureSet_VisibilityFeature());
        return message;
      }
    };
    function createBaseFeatureSetDefaults() {
      return { defaults: [], minimumEdition: 0, maximumEdition: 0 };
    }
    exports2.FeatureSetDefaults = {
      encode(message, writer = new wire_1.BinaryWriter()) {
        for (const v of message.defaults) {
          exports2.FeatureSetDefaults_FeatureSetEditionDefault.encode(v, writer.uint32(10).fork()).join();
        }
        if (message.minimumEdition !== 0) {
          writer.uint32(32).int32(message.minimumEdition);
        }
        if (message.maximumEdition !== 0) {
          writer.uint32(40).int32(message.maximumEdition);
        }
        if (message._unknownFields !== void 0) {
          for (const [key, values] of Object.entries(message._unknownFields)) {
            const tag = parseInt(key, 10);
            for (const value of values) {
              writer.uint32(tag).raw(value);
            }
          }
        }
        return writer;
      },
      decode(input, length) {
        const reader = input instanceof wire_1.BinaryReader ? input : new wire_1.BinaryReader(input);
        let end = length === void 0 ? reader.len : reader.pos + length;
        const message = Object.create(createBaseFeatureSetDefaults());
        while (reader.pos < end) {
          const tag = reader.uint32();
          switch (tag >>> 3) {
            case 1:
              if (tag !== 10) {
                break;
              }
              message.defaults.push(exports2.FeatureSetDefaults_FeatureSetEditionDefault.decode(reader, reader.uint32()));
              continue;
            case 4:
              if (tag !== 32) {
                break;
              }
              message.minimumEdition = reader.int32();
              continue;
            case 5:
              if (tag !== 40) {
                break;
              }
              message.maximumEdition = reader.int32();
              continue;
          }
          if ((tag & 7) === 4 || tag === 0) {
            break;
          }
          const buf = reader.skip(tag & 7);
          if (message._unknownFields === void 0) {
            message._unknownFields = {};
          }
          const list = message._unknownFields[tag];
          if (list === void 0) {
            message._unknownFields[tag] = [buf];
          } else {
            list.push(buf);
          }
        }
        return message;
      },
      fromJSON(object) {
        return {
          defaults: globalThis.Array.isArray(object?.defaults) ? object.defaults.map((e) => exports2.FeatureSetDefaults_FeatureSetEditionDefault.fromJSON(e)) : [],
          minimumEdition: isSet(object.minimumEdition) ? editionFromJSON(object.minimumEdition) : 0,
          maximumEdition: isSet(object.maximumEdition) ? editionFromJSON(object.maximumEdition) : 0
        };
      },
      toJSON(message) {
        const obj = {};
        if (message.defaults?.length) {
          obj.defaults = message.defaults.map((e) => exports2.FeatureSetDefaults_FeatureSetEditionDefault.toJSON(e));
        }
        if (message.minimumEdition !== 0) {
          obj.minimumEdition = editionToJSON(message.minimumEdition);
        }
        if (message.maximumEdition !== 0) {
          obj.maximumEdition = editionToJSON(message.maximumEdition);
        }
        return obj;
      },
      create(base) {
        return exports2.FeatureSetDefaults.fromPartial(base ?? {});
      },
      fromPartial(object) {
        const message = Object.create(createBaseFeatureSetDefaults());
        message.defaults = object.defaults?.map((e) => exports2.FeatureSetDefaults_FeatureSetEditionDefault.fromPartial(e)) || [];
        message.minimumEdition = object.minimumEdition ?? 0;
        message.maximumEdition = object.maximumEdition ?? 0;
        return message;
      }
    };
    function createBaseFeatureSetDefaults_FeatureSetEditionDefault() {
      return { edition: 0, overridableFeatures: void 0, fixedFeatures: void 0 };
    }
    exports2.FeatureSetDefaults_FeatureSetEditionDefault = {
      encode(message, writer = new wire_1.BinaryWriter()) {
        if (message.edition !== 0) {
          writer.uint32(24).int32(message.edition);
        }
        if (message.overridableFeatures !== void 0) {
          exports2.FeatureSet.encode(message.overridableFeatures, writer.uint32(34).fork()).join();
        }
        if (message.fixedFeatures !== void 0) {
          exports2.FeatureSet.encode(message.fixedFeatures, writer.uint32(42).fork()).join();
        }
        if (message._unknownFields !== void 0) {
          for (const [key, values] of Object.entries(message._unknownFields)) {
            const tag = parseInt(key, 10);
            for (const value of values) {
              writer.uint32(tag).raw(value);
            }
          }
        }
        return writer;
      },
      decode(input, length) {
        const reader = input instanceof wire_1.BinaryReader ? input : new wire_1.BinaryReader(input);
        let end = length === void 0 ? reader.len : reader.pos + length;
        const message = Object.create(createBaseFeatureSetDefaults_FeatureSetEditionDefault());
        while (reader.pos < end) {
          const tag = reader.uint32();
          switch (tag >>> 3) {
            case 3:
              if (tag !== 24) {
                break;
              }
              message.edition = reader.int32();
              continue;
            case 4:
              if (tag !== 34) {
                break;
              }
              message.overridableFeatures = exports2.FeatureSet.decode(reader, reader.uint32());
              continue;
            case 5:
              if (tag !== 42) {
                break;
              }
              message.fixedFeatures = exports2.FeatureSet.decode(reader, reader.uint32());
              continue;
          }
          if ((tag & 7) === 4 || tag === 0) {
            break;
          }
          const buf = reader.skip(tag & 7);
          if (message._unknownFields === void 0) {
            message._unknownFields = {};
          }
          const list = message._unknownFields[tag];
          if (list === void 0) {
            message._unknownFields[tag] = [buf];
          } else {
            list.push(buf);
          }
        }
        return message;
      },
      fromJSON(object) {
        return {
          edition: isSet(object.edition) ? editionFromJSON(object.edition) : 0,
          overridableFeatures: isSet(object.overridableFeatures) ? exports2.FeatureSet.fromJSON(object.overridableFeatures) : void 0,
          fixedFeatures: isSet(object.fixedFeatures) ? exports2.FeatureSet.fromJSON(object.fixedFeatures) : void 0
        };
      },
      toJSON(message) {
        const obj = {};
        if (message.edition !== 0) {
          obj.edition = editionToJSON(message.edition);
        }
        if (message.overridableFeatures !== void 0) {
          obj.overridableFeatures = exports2.FeatureSet.toJSON(message.overridableFeatures);
        }
        if (message.fixedFeatures !== void 0) {
          obj.fixedFeatures = exports2.FeatureSet.toJSON(message.fixedFeatures);
        }
        return obj;
      },
      create(base) {
        return exports2.FeatureSetDefaults_FeatureSetEditionDefault.fromPartial(base ?? {});
      },
      fromPartial(object) {
        const message = Object.create(createBaseFeatureSetDefaults_FeatureSetEditionDefault());
        message.edition = object.edition ?? 0;
        message.overridableFeatures = object.overridableFeatures !== void 0 && object.overridableFeatures !== null ? exports2.FeatureSet.fromPartial(object.overridableFeatures) : void 0;
        message.fixedFeatures = object.fixedFeatures !== void 0 && object.fixedFeatures !== null ? exports2.FeatureSet.fromPartial(object.fixedFeatures) : void 0;
        return message;
      }
    };
    function createBaseSourceCodeInfo() {
      return { location: [] };
    }
    exports2.SourceCodeInfo = {
      encode(message, writer = new wire_1.BinaryWriter()) {
        for (const v of message.location) {
          exports2.SourceCodeInfo_Location.encode(v, writer.uint32(10).fork()).join();
        }
        if (message._unknownFields !== void 0) {
          for (const [key, values] of Object.entries(message._unknownFields)) {
            const tag = parseInt(key, 10);
            for (const value of values) {
              writer.uint32(tag).raw(value);
            }
          }
        }
        return writer;
      },
      setExtension(message, extension, value) {
        const encoded = extension.encode(value);
        if (message._unknownFields !== void 0) {
          delete message._unknownFields[extension.tag];
          if (extension.singularTag !== void 0) {
            delete message._unknownFields[extension.singularTag];
          }
        }
        if (encoded.length !== 0) {
          if (message._unknownFields === void 0) {
            message._unknownFields = {};
          }
          message._unknownFields[extension.tag] = encoded;
        }
      },
      decode(input, length) {
        const reader = input instanceof wire_1.BinaryReader ? input : new wire_1.BinaryReader(input);
        let end = length === void 0 ? reader.len : reader.pos + length;
        const message = Object.create(createBaseSourceCodeInfo());
        while (reader.pos < end) {
          const tag = reader.uint32();
          switch (tag >>> 3) {
            case 1:
              if (tag !== 10) {
                break;
              }
              message.location.push(exports2.SourceCodeInfo_Location.decode(reader, reader.uint32()));
              continue;
          }
          if ((tag & 7) === 4 || tag === 0) {
            break;
          }
          const buf = reader.skip(tag & 7);
          if (message._unknownFields === void 0) {
            message._unknownFields = {};
          }
          const list = message._unknownFields[tag];
          if (list === void 0) {
            message._unknownFields[tag] = [buf];
          } else {
            list.push(buf);
          }
        }
        return message;
      },
      getExtension(message, extension) {
        let results = void 0;
        if (message._unknownFields === void 0) {
          return void 0;
        }
        let list = message._unknownFields[extension.tag];
        if (list !== void 0) {
          results = extension.decode(extension.tag, list);
        }
        if (extension.singularTag === void 0) {
          return results;
        }
        list = message._unknownFields[extension.singularTag];
        if (list !== void 0) {
          const results2 = extension.decode(extension.singularTag, list);
          if (results !== void 0 && results.length !== 0) {
            results = results.concat(results2);
          } else {
            results = results2;
          }
        }
        return results;
      },
      fromJSON(object) {
        return {
          location: globalThis.Array.isArray(object?.location) ? object.location.map((e) => exports2.SourceCodeInfo_Location.fromJSON(e)) : []
        };
      },
      toJSON(message) {
        const obj = {};
        if (message.location?.length) {
          obj.location = message.location.map((e) => exports2.SourceCodeInfo_Location.toJSON(e));
        }
        return obj;
      },
      create(base) {
        return exports2.SourceCodeInfo.fromPartial(base ?? {});
      },
      fromPartial(object) {
        const message = Object.create(createBaseSourceCodeInfo());
        message.location = object.location?.map((e) => exports2.SourceCodeInfo_Location.fromPartial(e)) || [];
        return message;
      }
    };
    function createBaseSourceCodeInfo_Location() {
      return { path: [], span: [], leadingComments: "", trailingComments: "", leadingDetachedComments: [] };
    }
    exports2.SourceCodeInfo_Location = {
      encode(message, writer = new wire_1.BinaryWriter()) {
        writer.uint32(10).fork();
        for (const v of message.path) {
          writer.int32(v);
        }
        writer.join();
        writer.uint32(18).fork();
        for (const v of message.span) {
          writer.int32(v);
        }
        writer.join();
        if (message.leadingComments !== "") {
          writer.uint32(26).string(message.leadingComments);
        }
        if (message.trailingComments !== "") {
          writer.uint32(34).string(message.trailingComments);
        }
        for (const v of message.leadingDetachedComments) {
          writer.uint32(50).string(v);
        }
        if (message._unknownFields !== void 0) {
          for (const [key, values] of Object.entries(message._unknownFields)) {
            const tag = parseInt(key, 10);
            for (const value of values) {
              writer.uint32(tag).raw(value);
            }
          }
        }
        return writer;
      },
      decode(input, length) {
        const reader = input instanceof wire_1.BinaryReader ? input : new wire_1.BinaryReader(input);
        let end = length === void 0 ? reader.len : reader.pos + length;
        const message = Object.create(createBaseSourceCodeInfo_Location());
        while (reader.pos < end) {
          const tag = reader.uint32();
          switch (tag >>> 3) {
            case 1:
              if (tag === 8) {
                message.path.push(reader.int32());
                continue;
              }
              if (tag === 10) {
                const end2 = reader.uint32() + reader.pos;
                while (reader.pos < end2) {
                  message.path.push(reader.int32());
                }
                continue;
              }
              break;
            case 2:
              if (tag === 16) {
                message.span.push(reader.int32());
                continue;
              }
              if (tag === 18) {
                const end2 = reader.uint32() + reader.pos;
                while (reader.pos < end2) {
                  message.span.push(reader.int32());
                }
                continue;
              }
              break;
            case 3:
              if (tag !== 26) {
                break;
              }
              message.leadingComments = reader.string();
              continue;
            case 4:
              if (tag !== 34) {
                break;
              }
              message.trailingComments = reader.string();
              continue;
            case 6:
              if (tag !== 50) {
                break;
              }
              message.leadingDetachedComments.push(reader.string());
              continue;
          }
          if ((tag & 7) === 4 || tag === 0) {
            break;
          }
          const buf = reader.skip(tag & 7);
          if (message._unknownFields === void 0) {
            message._unknownFields = {};
          }
          const list = message._unknownFields[tag];
          if (list === void 0) {
            message._unknownFields[tag] = [buf];
          } else {
            list.push(buf);
          }
        }
        return message;
      },
      fromJSON(object) {
        return {
          path: globalThis.Array.isArray(object?.path) ? object.path.map((e) => globalThis.Number(e)) : [],
          span: globalThis.Array.isArray(object?.span) ? object.span.map((e) => globalThis.Number(e)) : [],
          leadingComments: isSet(object.leadingComments) ? globalThis.String(object.leadingComments) : "",
          trailingComments: isSet(object.trailingComments) ? globalThis.String(object.trailingComments) : "",
          leadingDetachedComments: globalThis.Array.isArray(object?.leadingDetachedComments) ? object.leadingDetachedComments.map((e) => globalThis.String(e)) : []
        };
      },
      toJSON(message) {
        const obj = {};
        if (message.path?.length) {
          obj.path = message.path.map((e) => Math.round(e));
        }
        if (message.span?.length) {
          obj.span = message.span.map((e) => Math.round(e));
        }
        if (message.leadingComments !== "") {
          obj.leadingComments = message.leadingComments;
        }
        if (message.trailingComments !== "") {
          obj.trailingComments = message.trailingComments;
        }
        if (message.leadingDetachedComments?.length) {
          obj.leadingDetachedComments = message.leadingDetachedComments;
        }
        return obj;
      },
      create(base) {
        return exports2.SourceCodeInfo_Location.fromPartial(base ?? {});
      },
      fromPartial(object) {
        const message = Object.create(createBaseSourceCodeInfo_Location());
        message.path = object.path?.map((e) => e) || [];
        message.span = object.span?.map((e) => e) || [];
        message.leadingComments = object.leadingComments ?? "";
        message.trailingComments = object.trailingComments ?? "";
        message.leadingDetachedComments = object.leadingDetachedComments?.map((e) => e) || [];
        return message;
      }
    };
    function createBaseGeneratedCodeInfo() {
      return { annotation: [] };
    }
    exports2.GeneratedCodeInfo = {
      encode(message, writer = new wire_1.BinaryWriter()) {
        for (const v of message.annotation) {
          exports2.GeneratedCodeInfo_Annotation.encode(v, writer.uint32(10).fork()).join();
        }
        if (message._unknownFields !== void 0) {
          for (const [key, values] of Object.entries(message._unknownFields)) {
            const tag = parseInt(key, 10);
            for (const value of values) {
              writer.uint32(tag).raw(value);
            }
          }
        }
        return writer;
      },
      decode(input, length) {
        const reader = input instanceof wire_1.BinaryReader ? input : new wire_1.BinaryReader(input);
        let end = length === void 0 ? reader.len : reader.pos + length;
        const message = Object.create(createBaseGeneratedCodeInfo());
        while (reader.pos < end) {
          const tag = reader.uint32();
          switch (tag >>> 3) {
            case 1:
              if (tag !== 10) {
                break;
              }
              message.annotation.push(exports2.GeneratedCodeInfo_Annotation.decode(reader, reader.uint32()));
              continue;
          }
          if ((tag & 7) === 4 || tag === 0) {
            break;
          }
          const buf = reader.skip(tag & 7);
          if (message._unknownFields === void 0) {
            message._unknownFields = {};
          }
          const list = message._unknownFields[tag];
          if (list === void 0) {
            message._unknownFields[tag] = [buf];
          } else {
            list.push(buf);
          }
        }
        return message;
      },
      fromJSON(object) {
        return {
          annotation: globalThis.Array.isArray(object?.annotation) ? object.annotation.map((e) => exports2.GeneratedCodeInfo_Annotation.fromJSON(e)) : []
        };
      },
      toJSON(message) {
        const obj = {};
        if (message.annotation?.length) {
          obj.annotation = message.annotation.map((e) => exports2.GeneratedCodeInfo_Annotation.toJSON(e));
        }
        return obj;
      },
      create(base) {
        return exports2.GeneratedCodeInfo.fromPartial(base ?? {});
      },
      fromPartial(object) {
        const message = Object.create(createBaseGeneratedCodeInfo());
        message.annotation = object.annotation?.map((e) => exports2.GeneratedCodeInfo_Annotation.fromPartial(e)) || [];
        return message;
      }
    };
    function createBaseGeneratedCodeInfo_Annotation() {
      return { path: [], sourceFile: "", begin: 0, end: 0, semantic: 0 };
    }
    exports2.GeneratedCodeInfo_Annotation = {
      encode(message, writer = new wire_1.BinaryWriter()) {
        writer.uint32(10).fork();
        for (const v of message.path) {
          writer.int32(v);
        }
        writer.join();
        if (message.sourceFile !== "") {
          writer.uint32(18).string(message.sourceFile);
        }
        if (message.begin !== 0) {
          writer.uint32(24).int32(message.begin);
        }
        if (message.end !== 0) {
          writer.uint32(32).int32(message.end);
        }
        if (message.semantic !== 0) {
          writer.uint32(40).int32(message.semantic);
        }
        if (message._unknownFields !== void 0) {
          for (const [key, values] of Object.entries(message._unknownFields)) {
            const tag = parseInt(key, 10);
            for (const value of values) {
              writer.uint32(tag).raw(value);
            }
          }
        }
        return writer;
      },
      decode(input, length) {
        const reader = input instanceof wire_1.BinaryReader ? input : new wire_1.BinaryReader(input);
        let end = length === void 0 ? reader.len : reader.pos + length;
        const message = Object.create(createBaseGeneratedCodeInfo_Annotation());
        while (reader.pos < end) {
          const tag = reader.uint32();
          switch (tag >>> 3) {
            case 1:
              if (tag === 8) {
                message.path.push(reader.int32());
                continue;
              }
              if (tag === 10) {
                const end2 = reader.uint32() + reader.pos;
                while (reader.pos < end2) {
                  message.path.push(reader.int32());
                }
                continue;
              }
              break;
            case 2:
              if (tag !== 18) {
                break;
              }
              message.sourceFile = reader.string();
              continue;
            case 3:
              if (tag !== 24) {
                break;
              }
              message.begin = reader.int32();
              continue;
            case 4:
              if (tag !== 32) {
                break;
              }
              message.end = reader.int32();
              continue;
            case 5:
              if (tag !== 40) {
                break;
              }
              message.semantic = reader.int32();
              continue;
          }
          if ((tag & 7) === 4 || tag === 0) {
            break;
          }
          const buf = reader.skip(tag & 7);
          if (message._unknownFields === void 0) {
            message._unknownFields = {};
          }
          const list = message._unknownFields[tag];
          if (list === void 0) {
            message._unknownFields[tag] = [buf];
          } else {
            list.push(buf);
          }
        }
        return message;
      },
      fromJSON(object) {
        return {
          path: globalThis.Array.isArray(object?.path) ? object.path.map((e) => globalThis.Number(e)) : [],
          sourceFile: isSet(object.sourceFile) ? globalThis.String(object.sourceFile) : "",
          begin: isSet(object.begin) ? globalThis.Number(object.begin) : 0,
          end: isSet(object.end) ? globalThis.Number(object.end) : 0,
          semantic: isSet(object.semantic) ? generatedCodeInfo_Annotation_SemanticFromJSON(object.semantic) : 0
        };
      },
      toJSON(message) {
        const obj = {};
        if (message.path?.length) {
          obj.path = message.path.map((e) => Math.round(e));
        }
        if (message.sourceFile !== "") {
          obj.sourceFile = message.sourceFile;
        }
        if (message.begin !== 0) {
          obj.begin = Math.round(message.begin);
        }
        if (message.end !== 0) {
          obj.end = Math.round(message.end);
        }
        if (message.semantic !== 0) {
          obj.semantic = generatedCodeInfo_Annotation_SemanticToJSON(message.semantic);
        }
        return obj;
      },
      create(base) {
        return exports2.GeneratedCodeInfo_Annotation.fromPartial(base ?? {});
      },
      fromPartial(object) {
        const message = Object.create(createBaseGeneratedCodeInfo_Annotation());
        message.path = object.path?.map((e) => e) || [];
        message.sourceFile = object.sourceFile ?? "";
        message.begin = object.begin ?? 0;
        message.end = object.end ?? 0;
        message.semantic = object.semantic ?? 0;
        return message;
      }
    };
    function bytesFromBase64(b64) {
      if (globalThis.Buffer) {
        return Uint8Array.from(globalThis.Buffer.from(b64, "base64"));
      } else {
        const bin = globalThis.atob(b64);
        const arr = new Uint8Array(bin.length);
        for (let i = 0; i < bin.length; ++i) {
          arr[i] = bin.charCodeAt(i);
        }
        return arr;
      }
    }
    function base64FromBytes(arr) {
      if (globalThis.Buffer) {
        return globalThis.Buffer.from(arr).toString("base64");
      } else {
        const bin = [];
        arr.forEach((byte) => {
          bin.push(globalThis.String.fromCharCode(byte));
        });
        return globalThis.btoa(bin.join(""));
      }
    }
    function longToNumber(int64) {
      const num = globalThis.Number(int64.toString());
      if (num > globalThis.Number.MAX_SAFE_INTEGER) {
        throw new globalThis.Error("Value is larger than Number.MAX_SAFE_INTEGER");
      }
      if (num < globalThis.Number.MIN_SAFE_INTEGER) {
        throw new globalThis.Error("Value is smaller than Number.MIN_SAFE_INTEGER");
      }
      return num;
    }
    function isSet(value) {
      return value !== null && value !== void 0;
    }
  }
});

// hardware/generic/goldfish/emulator/ui/aquarium/node_modules/ts-proto-descriptors/dist/google/protobuf/compiler/plugin.js
var require_plugin = __commonJS({
  "hardware/generic/goldfish/emulator/ui/aquarium/node_modules/ts-proto-descriptors/dist/google/protobuf/compiler/plugin.js"(exports2) {
    "use strict";
    Object.defineProperty(exports2, "__esModule", { value: true });
    exports2.CodeGeneratorResponse_File = exports2.CodeGeneratorResponse = exports2.CodeGeneratorRequest = exports2.Version = exports2.codeGeneratorResponse_FeatureToJSON = exports2.codeGeneratorResponse_FeatureFromJSON = exports2.CodeGeneratorResponse_Feature = void 0;
    var wire_1 = require_wire();
    var descriptor_1 = require_descriptor();
    var CodeGeneratorResponse_Feature;
    (function(CodeGeneratorResponse_Feature2) {
      CodeGeneratorResponse_Feature2[CodeGeneratorResponse_Feature2["FEATURE_NONE"] = 0] = "FEATURE_NONE";
      CodeGeneratorResponse_Feature2[CodeGeneratorResponse_Feature2["FEATURE_PROTO3_OPTIONAL"] = 1] = "FEATURE_PROTO3_OPTIONAL";
      CodeGeneratorResponse_Feature2[CodeGeneratorResponse_Feature2["FEATURE_SUPPORTS_EDITIONS"] = 2] = "FEATURE_SUPPORTS_EDITIONS";
      CodeGeneratorResponse_Feature2[CodeGeneratorResponse_Feature2["UNRECOGNIZED"] = -1] = "UNRECOGNIZED";
    })(CodeGeneratorResponse_Feature || (exports2.CodeGeneratorResponse_Feature = CodeGeneratorResponse_Feature = {}));
    function codeGeneratorResponse_FeatureFromJSON(object) {
      switch (object) {
        case 0:
        case "FEATURE_NONE":
          return CodeGeneratorResponse_Feature.FEATURE_NONE;
        case 1:
        case "FEATURE_PROTO3_OPTIONAL":
          return CodeGeneratorResponse_Feature.FEATURE_PROTO3_OPTIONAL;
        case 2:
        case "FEATURE_SUPPORTS_EDITIONS":
          return CodeGeneratorResponse_Feature.FEATURE_SUPPORTS_EDITIONS;
        case -1:
        case "UNRECOGNIZED":
        default:
          return CodeGeneratorResponse_Feature.UNRECOGNIZED;
      }
    }
    exports2.codeGeneratorResponse_FeatureFromJSON = codeGeneratorResponse_FeatureFromJSON;
    function codeGeneratorResponse_FeatureToJSON(object) {
      switch (object) {
        case CodeGeneratorResponse_Feature.FEATURE_NONE:
          return "FEATURE_NONE";
        case CodeGeneratorResponse_Feature.FEATURE_PROTO3_OPTIONAL:
          return "FEATURE_PROTO3_OPTIONAL";
        case CodeGeneratorResponse_Feature.FEATURE_SUPPORTS_EDITIONS:
          return "FEATURE_SUPPORTS_EDITIONS";
        case CodeGeneratorResponse_Feature.UNRECOGNIZED:
        default:
          return "UNRECOGNIZED";
      }
    }
    exports2.codeGeneratorResponse_FeatureToJSON = codeGeneratorResponse_FeatureToJSON;
    function createBaseVersion() {
      return { major: 0, minor: 0, patch: 0, suffix: "" };
    }
    exports2.Version = {
      encode(message, writer = new wire_1.BinaryWriter()) {
        if (message.major !== 0) {
          writer.uint32(8).int32(message.major);
        }
        if (message.minor !== 0) {
          writer.uint32(16).int32(message.minor);
        }
        if (message.patch !== 0) {
          writer.uint32(24).int32(message.patch);
        }
        if (message.suffix !== "") {
          writer.uint32(34).string(message.suffix);
        }
        if (message._unknownFields !== void 0) {
          for (const [key, values] of Object.entries(message._unknownFields)) {
            const tag = parseInt(key, 10);
            for (const value of values) {
              writer.uint32(tag).raw(value);
            }
          }
        }
        return writer;
      },
      decode(input, length) {
        const reader = input instanceof wire_1.BinaryReader ? input : new wire_1.BinaryReader(input);
        let end = length === void 0 ? reader.len : reader.pos + length;
        const message = Object.create(createBaseVersion());
        while (reader.pos < end) {
          const tag = reader.uint32();
          switch (tag >>> 3) {
            case 1:
              if (tag !== 8) {
                break;
              }
              message.major = reader.int32();
              continue;
            case 2:
              if (tag !== 16) {
                break;
              }
              message.minor = reader.int32();
              continue;
            case 3:
              if (tag !== 24) {
                break;
              }
              message.patch = reader.int32();
              continue;
            case 4:
              if (tag !== 34) {
                break;
              }
              message.suffix = reader.string();
              continue;
          }
          if ((tag & 7) === 4 || tag === 0) {
            break;
          }
          const buf = reader.skip(tag & 7);
          if (message._unknownFields === void 0) {
            message._unknownFields = {};
          }
          const list = message._unknownFields[tag];
          if (list === void 0) {
            message._unknownFields[tag] = [buf];
          } else {
            list.push(buf);
          }
        }
        return message;
      },
      fromJSON(object) {
        return {
          major: isSet(object.major) ? globalThis.Number(object.major) : 0,
          minor: isSet(object.minor) ? globalThis.Number(object.minor) : 0,
          patch: isSet(object.patch) ? globalThis.Number(object.patch) : 0,
          suffix: isSet(object.suffix) ? globalThis.String(object.suffix) : ""
        };
      },
      toJSON(message) {
        const obj = {};
        if (message.major !== 0) {
          obj.major = Math.round(message.major);
        }
        if (message.minor !== 0) {
          obj.minor = Math.round(message.minor);
        }
        if (message.patch !== 0) {
          obj.patch = Math.round(message.patch);
        }
        if (message.suffix !== "") {
          obj.suffix = message.suffix;
        }
        return obj;
      },
      create(base) {
        return exports2.Version.fromPartial(base ?? {});
      },
      fromPartial(object) {
        const message = Object.create(createBaseVersion());
        message.major = object.major ?? 0;
        message.minor = object.minor ?? 0;
        message.patch = object.patch ?? 0;
        message.suffix = object.suffix ?? "";
        return message;
      }
    };
    function createBaseCodeGeneratorRequest() {
      return { fileToGenerate: [], parameter: "", protoFile: [], sourceFileDescriptors: [], compilerVersion: void 0 };
    }
    exports2.CodeGeneratorRequest = {
      encode(message, writer = new wire_1.BinaryWriter()) {
        for (const v of message.fileToGenerate) {
          writer.uint32(10).string(v);
        }
        if (message.parameter !== "") {
          writer.uint32(18).string(message.parameter);
        }
        for (const v of message.protoFile) {
          descriptor_1.FileDescriptorProto.encode(v, writer.uint32(122).fork()).join();
        }
        for (const v of message.sourceFileDescriptors) {
          descriptor_1.FileDescriptorProto.encode(v, writer.uint32(138).fork()).join();
        }
        if (message.compilerVersion !== void 0) {
          exports2.Version.encode(message.compilerVersion, writer.uint32(26).fork()).join();
        }
        if (message._unknownFields !== void 0) {
          for (const [key, values] of Object.entries(message._unknownFields)) {
            const tag = parseInt(key, 10);
            for (const value of values) {
              writer.uint32(tag).raw(value);
            }
          }
        }
        return writer;
      },
      decode(input, length) {
        const reader = input instanceof wire_1.BinaryReader ? input : new wire_1.BinaryReader(input);
        let end = length === void 0 ? reader.len : reader.pos + length;
        const message = Object.create(createBaseCodeGeneratorRequest());
        while (reader.pos < end) {
          const tag = reader.uint32();
          switch (tag >>> 3) {
            case 1:
              if (tag !== 10) {
                break;
              }
              message.fileToGenerate.push(reader.string());
              continue;
            case 2:
              if (tag !== 18) {
                break;
              }
              message.parameter = reader.string();
              continue;
            case 15:
              if (tag !== 122) {
                break;
              }
              message.protoFile.push(descriptor_1.FileDescriptorProto.decode(reader, reader.uint32()));
              continue;
            case 17:
              if (tag !== 138) {
                break;
              }
              message.sourceFileDescriptors.push(descriptor_1.FileDescriptorProto.decode(reader, reader.uint32()));
              continue;
            case 3:
              if (tag !== 26) {
                break;
              }
              message.compilerVersion = exports2.Version.decode(reader, reader.uint32());
              continue;
          }
          if ((tag & 7) === 4 || tag === 0) {
            break;
          }
          const buf = reader.skip(tag & 7);
          if (message._unknownFields === void 0) {
            message._unknownFields = {};
          }
          const list = message._unknownFields[tag];
          if (list === void 0) {
            message._unknownFields[tag] = [buf];
          } else {
            list.push(buf);
          }
        }
        return message;
      },
      fromJSON(object) {
        return {
          fileToGenerate: globalThis.Array.isArray(object?.fileToGenerate) ? object.fileToGenerate.map((e) => globalThis.String(e)) : [],
          parameter: isSet(object.parameter) ? globalThis.String(object.parameter) : "",
          protoFile: globalThis.Array.isArray(object?.protoFile) ? object.protoFile.map((e) => descriptor_1.FileDescriptorProto.fromJSON(e)) : [],
          sourceFileDescriptors: globalThis.Array.isArray(object?.sourceFileDescriptors) ? object.sourceFileDescriptors.map((e) => descriptor_1.FileDescriptorProto.fromJSON(e)) : [],
          compilerVersion: isSet(object.compilerVersion) ? exports2.Version.fromJSON(object.compilerVersion) : void 0
        };
      },
      toJSON(message) {
        const obj = {};
        if (message.fileToGenerate?.length) {
          obj.fileToGenerate = message.fileToGenerate;
        }
        if (message.parameter !== "") {
          obj.parameter = message.parameter;
        }
        if (message.protoFile?.length) {
          obj.protoFile = message.protoFile.map((e) => descriptor_1.FileDescriptorProto.toJSON(e));
        }
        if (message.sourceFileDescriptors?.length) {
          obj.sourceFileDescriptors = message.sourceFileDescriptors.map((e) => descriptor_1.FileDescriptorProto.toJSON(e));
        }
        if (message.compilerVersion !== void 0) {
          obj.compilerVersion = exports2.Version.toJSON(message.compilerVersion);
        }
        return obj;
      },
      create(base) {
        return exports2.CodeGeneratorRequest.fromPartial(base ?? {});
      },
      fromPartial(object) {
        const message = Object.create(createBaseCodeGeneratorRequest());
        message.fileToGenerate = object.fileToGenerate?.map((e) => e) || [];
        message.parameter = object.parameter ?? "";
        message.protoFile = object.protoFile?.map((e) => descriptor_1.FileDescriptorProto.fromPartial(e)) || [];
        message.sourceFileDescriptors = object.sourceFileDescriptors?.map((e) => descriptor_1.FileDescriptorProto.fromPartial(e)) || [];
        message.compilerVersion = object.compilerVersion !== void 0 && object.compilerVersion !== null ? exports2.Version.fromPartial(object.compilerVersion) : void 0;
        return message;
      }
    };
    function createBaseCodeGeneratorResponse() {
      return { error: "", supportedFeatures: 0, minimumEdition: 0, maximumEdition: 0, file: [] };
    }
    exports2.CodeGeneratorResponse = {
      encode(message, writer = new wire_1.BinaryWriter()) {
        if (message.error !== "") {
          writer.uint32(10).string(message.error);
        }
        if (message.supportedFeatures !== 0) {
          writer.uint32(16).uint64(message.supportedFeatures);
        }
        if (message.minimumEdition !== 0) {
          writer.uint32(24).int32(message.minimumEdition);
        }
        if (message.maximumEdition !== 0) {
          writer.uint32(32).int32(message.maximumEdition);
        }
        for (const v of message.file) {
          exports2.CodeGeneratorResponse_File.encode(v, writer.uint32(122).fork()).join();
        }
        if (message._unknownFields !== void 0) {
          for (const [key, values] of Object.entries(message._unknownFields)) {
            const tag = parseInt(key, 10);
            for (const value of values) {
              writer.uint32(tag).raw(value);
            }
          }
        }
        return writer;
      },
      decode(input, length) {
        const reader = input instanceof wire_1.BinaryReader ? input : new wire_1.BinaryReader(input);
        let end = length === void 0 ? reader.len : reader.pos + length;
        const message = Object.create(createBaseCodeGeneratorResponse());
        while (reader.pos < end) {
          const tag = reader.uint32();
          switch (tag >>> 3) {
            case 1:
              if (tag !== 10) {
                break;
              }
              message.error = reader.string();
              continue;
            case 2:
              if (tag !== 16) {
                break;
              }
              message.supportedFeatures = longToNumber(reader.uint64());
              continue;
            case 3:
              if (tag !== 24) {
                break;
              }
              message.minimumEdition = reader.int32();
              continue;
            case 4:
              if (tag !== 32) {
                break;
              }
              message.maximumEdition = reader.int32();
              continue;
            case 15:
              if (tag !== 122) {
                break;
              }
              message.file.push(exports2.CodeGeneratorResponse_File.decode(reader, reader.uint32()));
              continue;
          }
          if ((tag & 7) === 4 || tag === 0) {
            break;
          }
          const buf = reader.skip(tag & 7);
          if (message._unknownFields === void 0) {
            message._unknownFields = {};
          }
          const list = message._unknownFields[tag];
          if (list === void 0) {
            message._unknownFields[tag] = [buf];
          } else {
            list.push(buf);
          }
        }
        return message;
      },
      fromJSON(object) {
        return {
          error: isSet(object.error) ? globalThis.String(object.error) : "",
          supportedFeatures: isSet(object.supportedFeatures) ? globalThis.Number(object.supportedFeatures) : 0,
          minimumEdition: isSet(object.minimumEdition) ? globalThis.Number(object.minimumEdition) : 0,
          maximumEdition: isSet(object.maximumEdition) ? globalThis.Number(object.maximumEdition) : 0,
          file: globalThis.Array.isArray(object?.file) ? object.file.map((e) => exports2.CodeGeneratorResponse_File.fromJSON(e)) : []
        };
      },
      toJSON(message) {
        const obj = {};
        if (message.error !== "") {
          obj.error = message.error;
        }
        if (message.supportedFeatures !== 0) {
          obj.supportedFeatures = Math.round(message.supportedFeatures);
        }
        if (message.minimumEdition !== 0) {
          obj.minimumEdition = Math.round(message.minimumEdition);
        }
        if (message.maximumEdition !== 0) {
          obj.maximumEdition = Math.round(message.maximumEdition);
        }
        if (message.file?.length) {
          obj.file = message.file.map((e) => exports2.CodeGeneratorResponse_File.toJSON(e));
        }
        return obj;
      },
      create(base) {
        return exports2.CodeGeneratorResponse.fromPartial(base ?? {});
      },
      fromPartial(object) {
        const message = Object.create(createBaseCodeGeneratorResponse());
        message.error = object.error ?? "";
        message.supportedFeatures = object.supportedFeatures ?? 0;
        message.minimumEdition = object.minimumEdition ?? 0;
        message.maximumEdition = object.maximumEdition ?? 0;
        message.file = object.file?.map((e) => exports2.CodeGeneratorResponse_File.fromPartial(e)) || [];
        return message;
      }
    };
    function createBaseCodeGeneratorResponse_File() {
      return { name: "", insertionPoint: "", content: "", generatedCodeInfo: void 0 };
    }
    exports2.CodeGeneratorResponse_File = {
      encode(message, writer = new wire_1.BinaryWriter()) {
        if (message.name !== "") {
          writer.uint32(10).string(message.name);
        }
        if (message.insertionPoint !== "") {
          writer.uint32(18).string(message.insertionPoint);
        }
        if (message.content !== "") {
          writer.uint32(122).string(message.content);
        }
        if (message.generatedCodeInfo !== void 0) {
          descriptor_1.GeneratedCodeInfo.encode(message.generatedCodeInfo, writer.uint32(130).fork()).join();
        }
        if (message._unknownFields !== void 0) {
          for (const [key, values] of Object.entries(message._unknownFields)) {
            const tag = parseInt(key, 10);
            for (const value of values) {
              writer.uint32(tag).raw(value);
            }
          }
        }
        return writer;
      },
      decode(input, length) {
        const reader = input instanceof wire_1.BinaryReader ? input : new wire_1.BinaryReader(input);
        let end = length === void 0 ? reader.len : reader.pos + length;
        const message = Object.create(createBaseCodeGeneratorResponse_File());
        while (reader.pos < end) {
          const tag = reader.uint32();
          switch (tag >>> 3) {
            case 1:
              if (tag !== 10) {
                break;
              }
              message.name = reader.string();
              continue;
            case 2:
              if (tag !== 18) {
                break;
              }
              message.insertionPoint = reader.string();
              continue;
            case 15:
              if (tag !== 122) {
                break;
              }
              message.content = reader.string();
              continue;
            case 16:
              if (tag !== 130) {
                break;
              }
              message.generatedCodeInfo = descriptor_1.GeneratedCodeInfo.decode(reader, reader.uint32());
              continue;
          }
          if ((tag & 7) === 4 || tag === 0) {
            break;
          }
          const buf = reader.skip(tag & 7);
          if (message._unknownFields === void 0) {
            message._unknownFields = {};
          }
          const list = message._unknownFields[tag];
          if (list === void 0) {
            message._unknownFields[tag] = [buf];
          } else {
            list.push(buf);
          }
        }
        return message;
      },
      fromJSON(object) {
        return {
          name: isSet(object.name) ? globalThis.String(object.name) : "",
          insertionPoint: isSet(object.insertionPoint) ? globalThis.String(object.insertionPoint) : "",
          content: isSet(object.content) ? globalThis.String(object.content) : "",
          generatedCodeInfo: isSet(object.generatedCodeInfo) ? descriptor_1.GeneratedCodeInfo.fromJSON(object.generatedCodeInfo) : void 0
        };
      },
      toJSON(message) {
        const obj = {};
        if (message.name !== "") {
          obj.name = message.name;
        }
        if (message.insertionPoint !== "") {
          obj.insertionPoint = message.insertionPoint;
        }
        if (message.content !== "") {
          obj.content = message.content;
        }
        if (message.generatedCodeInfo !== void 0) {
          obj.generatedCodeInfo = descriptor_1.GeneratedCodeInfo.toJSON(message.generatedCodeInfo);
        }
        return obj;
      },
      create(base) {
        return exports2.CodeGeneratorResponse_File.fromPartial(base ?? {});
      },
      fromPartial(object) {
        const message = Object.create(createBaseCodeGeneratorResponse_File());
        message.name = object.name ?? "";
        message.insertionPoint = object.insertionPoint ?? "";
        message.content = object.content ?? "";
        message.generatedCodeInfo = object.generatedCodeInfo !== void 0 && object.generatedCodeInfo !== null ? descriptor_1.GeneratedCodeInfo.fromPartial(object.generatedCodeInfo) : void 0;
        return message;
      }
    };
    function longToNumber(int64) {
      const num = globalThis.Number(int64.toString());
      if (num > globalThis.Number.MAX_SAFE_INTEGER) {
        throw new globalThis.Error("Value is larger than Number.MAX_SAFE_INTEGER");
      }
      if (num < globalThis.Number.MIN_SAFE_INTEGER) {
        throw new globalThis.Error("Value is smaller than Number.MIN_SAFE_INTEGER");
      }
      return num;
    }
    function isSet(value) {
      return value !== null && value !== void 0;
    }
  }
});

// hardware/generic/goldfish/emulator/ui/aquarium/node_modules/ts-proto-descriptors/dist/index.js
var require_dist = __commonJS({
  "hardware/generic/goldfish/emulator/ui/aquarium/node_modules/ts-proto-descriptors/dist/index.js"(exports2) {
    "use strict";
    var __createBinding = exports2 && exports2.__createBinding || (Object.create ? function(o, m, k, k2) {
      if (k2 === void 0) k2 = k;
      var desc = Object.getOwnPropertyDescriptor(m, k);
      if (!desc || ("get" in desc ? !m.__esModule : desc.writable || desc.configurable)) {
        desc = { enumerable: true, get: function() {
          return m[k];
        } };
      }
      Object.defineProperty(o, k2, desc);
    } : function(o, m, k, k2) {
      if (k2 === void 0) k2 = k;
      o[k2] = m[k];
    });
    var __exportStar = exports2 && exports2.__exportStar || function(m, exports3) {
      for (var p in m) if (p !== "default" && !Object.prototype.hasOwnProperty.call(exports3, p)) __createBinding(exports3, m, p);
    };
    Object.defineProperty(exports2, "__esModule", { value: true });
    __exportStar(require_descriptor(), exports2);
    __exportStar(require_plugin(), exports2);
  }
});

// hardware/generic/goldfish/emulator/ui/aquarium/node_modules/ts-poet/build/Node.js
var require_Node = __commonJS({
  "hardware/generic/goldfish/emulator/ui/aquarium/node_modules/ts-poet/build/Node.js"(exports2) {
    "use strict";
    Object.defineProperty(exports2, "__esModule", { value: true });
    exports2.Node = void 0;
    var Node = class {
    };
    exports2.Node = Node;
  }
});

// hardware/generic/goldfish/emulator/ui/aquarium/node_modules/ts-poet/build/utils.js
var require_utils = __commonJS({
  "hardware/generic/goldfish/emulator/ui/aquarium/node_modules/ts-poet/build/utils.js"(exports2) {
    "use strict";
    Object.defineProperty(exports2, "__esModule", { value: true });
    exports2.last = exports2.groupBy = void 0;
    function groupBy(list, fn, valueFn) {
      const result = {};
      list.forEach((o) => {
        var _a;
        const group = fn(o);
        (_a = result[group]) !== null && _a !== void 0 ? _a : result[group] = [];
        result[group].push(valueFn ? valueFn(o) : o);
      });
      return result;
    }
    exports2.groupBy = groupBy;
    function last(list) {
      return list[list.length - 1];
    }
    exports2.last = last;
  }
});

// hardware/generic/goldfish/emulator/ui/aquarium/node_modules/ts-poet/build/Import.js
var require_Import = __commonJS({
  "hardware/generic/goldfish/emulator/ui/aquarium/node_modules/ts-poet/build/Import.js"(exports2) {
    "use strict";
    var __createBinding = exports2 && exports2.__createBinding || (Object.create ? function(o, m, k, k2) {
      if (k2 === void 0) k2 = k;
      var desc = Object.getOwnPropertyDescriptor(m, k);
      if (!desc || ("get" in desc ? !m.__esModule : desc.writable || desc.configurable)) {
        desc = { enumerable: true, get: function() {
          return m[k];
        } };
      }
      Object.defineProperty(o, k2, desc);
    } : function(o, m, k, k2) {
      if (k2 === void 0) k2 = k;
      o[k2] = m[k];
    });
    var __setModuleDefault = exports2 && exports2.__setModuleDefault || (Object.create ? function(o, v) {
      Object.defineProperty(o, "default", { enumerable: true, value: v });
    } : function(o, v) {
      o["default"] = v;
    });
    var __importStar = exports2 && exports2.__importStar || function(mod) {
      if (mod && mod.__esModule) return mod;
      var result = {};
      if (mod != null) {
        for (var k in mod) if (k !== "default" && Object.prototype.hasOwnProperty.call(mod, k)) __createBinding(result, mod, k);
      }
      __setModuleDefault(result, mod);
      return result;
    };
    Object.defineProperty(exports2, "__esModule", { value: true });
    exports2.sameModule = exports2.maybeRelativePath = exports2.emitImports = exports2.SideEffect = exports2.ImportsAll = exports2.ImportsDefault = exports2.ImportsName = exports2.Imported = exports2.Implicit = exports2.Import = exports2.importType = void 0;
    var path = __importStar(require("path"));
    var Node_1 = require_Node();
    var utils_12 = require_utils();
    var typeImportMarker = "(?:t:)?";
    var modulePattern = `.+`;
    var identPattern = `(?:(?:[a-zA-Z][_a-zA-Z0-9]*)|(?:[_a-zA-Z][_a-zA-Z0-9]+))`;
    exports2.importType = "[*@+=]";
    var importPattern = `^(${typeImportMarker}${identPattern})(\\.${identPattern})?(${exports2.importType})(${modulePattern})`;
    var sourceIdentPattern = `(?:(?:${identPattern}:)?)`;
    var sourceImportPattern = `^(${typeImportMarker}${sourceIdentPattern}${identPattern})(\\.${identPattern})?(@)(${modulePattern})`;
    var Import = class _Import extends Node_1.Node {
      /**
       * Parses a symbol reference pattern to create a symbol. The pattern
       * allows the simple definition of all symbol types including any possible
       * import variation. If the spec to parse does not follow the proper format
       * an implicit symbol is created from the unparsed spec.
       *
       * Pattern: `symbolName? importType modulePath`
       *
       * Where:
       *
       * - `symbolName` is any legal JS/TS symbol. If none, we use the last part of the module path as a guess.
       * - `importType` is one of `@` or `*` or `+`, where:
       *    - `@` is a named import
       *       - `Foo@bar` becomes `import { Foo } from 'bar'`
       *    - `*` is a star import,
       *       - `Foo*foo` becomes `import * as Foo from 'foo'`
       *    - `+` is an implicit import
       *       - E.g. `Foo+foo` becomes `import 'foo'`
       * - `modulePath` is a path
       *    - E.g. `<filename>(/<filename)*`
       *
       *
       * @param spec Symbol spec to parse.
       * @return Parsed symbol specification
       */
      static from(spec) {
        var _a;
        let matched = spec.match(importPattern);
        if (matched === null) {
          matched = spec.match(sourceImportPattern);
        }
        if (matched != null) {
          const modulePath = matched[4];
          const childSymbol = ((_a = matched[2]) === null || _a === void 0 ? void 0 : _a.substring(1)) || void 0;
          const kind = matched[3] || "@";
          const symbolName = matched[1] || "";
          switch (kind) {
            case "*":
              return _Import.importsAll(symbolName, modulePath);
            case "@":
              const isTypeImport = symbolName.startsWith("t:");
              let exportedNames;
              if (isTypeImport) {
                exportedNames = symbolName.substring(2).split(":");
              } else {
                exportedNames = symbolName.split(":");
              }
              const exportedName = exportedNames.pop();
              const sourceExportedName = exportedNames[0];
              return _Import.importsName(exportedName, modulePath, isTypeImport, sourceExportedName, childSymbol);
            case "=":
              return _Import.importsDefault(symbolName, modulePath);
            case "+":
              return _Import.sideEffect(symbolName, modulePath);
            default:
              throw new Error("Invalid import kind character");
          }
        }
        return _Import.implicit(spec);
      }
      static fromMaybeString(spec) {
        return typeof spec === "string" ? _Import.from(spec) : spec;
      }
      constructor(symbol) {
        super();
        this.symbol = symbol;
      }
      toCodeString() {
        return this.symbol;
      }
      get childNodes() {
        return [];
      }
      /**
       * Creates an import of all the modules exported symbols as a single
       * local named symbol
       *
       * e.g. `import * as Engine from 'templates';`
       *
       * @param localName The local name of the imported symbols
       * @param from The module to import the symbols from
       */
      static importsAll(localName, from) {
        return new ImportsAll(localName, from);
      }
      /**
       * Creates an import of a single named symbol from the module's exported
       * symbols.
       *
       * - e.g. `import { Engine } from 'templates';`
       * - e.g. `import { Foo as Bar } from 'templates';`
       *
       * @param exportedName The symbol that is both exported and imported
       * @param from The module the symbol is exported from
       * @param typeImport whether this is an `import type` import
       * @param sourceSymbol The symbol as exported from the module, i.e. if we're doing a `Foo as Bar` rename
       * @param childSymbol The additional `Transaction` symbol in a `Knex.Transaction` import
       */
      static importsName(exportedName, from, typeImport, sourceSymbol, childSymbol) {
        return new ImportsName(exportedName, from, sourceSymbol, typeImport, childSymbol);
      }
      /**
       * Creates a symbol that is brought in as a side effect of
       * an import.
       *
       * e.g. `import 'mocha'`
       *
       * @param symbolName The symbol to be imported
       * @param from The entire import that does the augmentation
       */
      static sideEffect(symbolName, from) {
        return new SideEffect(symbolName, from);
      }
      /**
       * An implied symbol that does no tracking of imports
       *
       * @param name The implicit symbol name
       */
      static implicit(name) {
        return new Implicit(name);
      }
      /**
       * Creates an import of a single named symbol from the module's exported
       * default.
       *
       * e.g. `import Engine from 'engine';`
       *
       * @param exportedName The symbol that is both exported and imported
       * @param from The module the symbol is exported from
       */
      static importsDefault(exportedName, from) {
        return new ImportsDefault(exportedName, from);
      }
    };
    exports2.Import = Import;
    var Implicit = class extends Import {
      constructor(symbol) {
        super(symbol);
        this.source = void 0;
      }
    };
    exports2.Implicit = Implicit;
    var Imported = class extends Import {
      /** The symbol is the imported symbol, i.e. `BarClass`, and source is the path it comes from. */
      constructor(symbol, source) {
        super(source);
        this.symbol = symbol;
        this.source = source;
      }
    };
    exports2.Imported = Imported;
    var ImportsName = class extends Imported {
      /**
       * @param symbol
       * @param source
       * @param sourceSymbol is the optional original symbol, i.e. if we're renaming the symbol it is `Engine`
       * @param typeImport whether this is an `import type` import
       * @param childSymbol the `Transaction` in a `Knex.Transaction`
       */
      constructor(symbol, source, sourceSymbol, typeImport, childSymbol) {
        super(symbol, source);
        this.sourceSymbol = sourceSymbol;
        this.typeImport = typeImport;
        this.childSymbol = childSymbol;
      }
      toImportPiece(skipTypeImport = false) {
        const maybeTypePrefix = this.typeImport && !skipTypeImport ? "type " : "";
        return maybeTypePrefix + (this.sourceSymbol ? `${this.sourceSymbol} as ${this.symbol}` : this.symbol);
      }
      toCodeString() {
        return this.childSymbol ? `${this.symbol}.${this.childSymbol}` : this.symbol;
      }
    };
    exports2.ImportsName = ImportsName;
    var ImportsDefault = class extends Imported {
      constructor(symbol, source) {
        super(symbol, source);
      }
    };
    exports2.ImportsDefault = ImportsDefault;
    var ImportsAll = class extends Imported {
      constructor(symbol, source) {
        super(symbol, source);
      }
    };
    exports2.ImportsAll = ImportsAll;
    var SideEffect = class extends Imported {
      constructor(symbol, source) {
        super(symbol, source);
      }
    };
    exports2.SideEffect = SideEffect;
    function emitImports(imports, ourModulePath, importMappings, forceRequireImports, importExtensions) {
      if (imports.length == 0) {
        return "";
      }
      let result = "";
      const importsByModule = (0, utils_12.groupBy)(imports.filter((it) => it.source !== void 0 && // Ignore imports that are in our own file
      !(it instanceof ImportsName && it.definedIn && sameModule(it.definedIn, ourModulePath))), (it) => it.source);
      Object.entries(importsByModule).forEach(([modulePath, imports2]) => {
        if (sameModule(ourModulePath, modulePath))
          return;
        if (modulePath in importMappings) {
          modulePath = importMappings[modulePath];
        }
        const importPath = maybeAdjustExtension(maybeRelativePath(ourModulePath, modulePath), importExtensions);
        unique(filterInstances(imports2, ImportsAll).map((i) => i.symbol)).forEach((symbol) => {
          result += `import * as ${symbol} from '${importPath}';
`;
        });
        const symbolImports = filterInstancesAnd(imports2, ImportsName, (it) => !it.typeImport);
        const typeImports = filterInstancesAnd(imports2, ImportsName, (it) => {
          return !!it.typeImport && !symbolImports.some((s) => s.symbol === it.symbol);
        });
        const useSingleTypeImport = typeImports.length > 0 && symbolImports.length === 0;
        const namedImports = useSingleTypeImport ? unique(typeImports.map((it) => it.toImportPiece(true))) : unique([...typeImports, ...symbolImports].map((it) => it.toImportPiece()));
        const defaultImports = unique(filterInstances(imports2, ImportsDefault).map((it) => it.symbol));
        if (forceRequireImports.includes(modulePath) && defaultImports.length > 0) {
          result += `import ${defaultImports[0]} = require('${importPath}');
`;
        } else if (namedImports.length > 0 || defaultImports.length > 0) {
          const namesPart = namedImports.length > 0 ? [`{ ${namedImports.join(", ")} }`] : [];
          const defPart = defaultImports.length > 0 ? [defaultImports[0]] : [];
          result += `import${useSingleTypeImport ? " type" : ""} ${[...defPart, ...namesPart].join(", ")} from '${importPath}';
`;
        }
      });
      const sideEffectImports = (0, utils_12.groupBy)(filterInstances(imports, SideEffect), (a) => a.source);
      Object.keys(sideEffectImports).forEach((it) => result += `import '${it}';
`);
      return result;
    }
    exports2.emitImports = emitImports;
    function filterInstances(list, t) {
      return list.filter((e) => e instanceof t);
    }
    function filterInstancesAnd(list, t, fn) {
      return list.filter((e) => e instanceof t && fn(e));
    }
    function unique(list) {
      return [...new Set(list)];
    }
    function maybeRelativePath(outputPath, importPath) {
      if (!importPath.startsWith("./")) {
        return importPath;
      }
      importPath = path.normalize(importPath);
      outputPath = path.normalize(outputPath);
      const outputPathDir = path.dirname(outputPath);
      if (outputPathDir === importPath) {
        return ".." + path.sep + path.basename(importPath);
      }
      let relativePath = path.relative(outputPathDir, importPath).split(path.sep).join(path.posix.sep);
      if (!relativePath.startsWith(".")) {
        relativePath = "./" + relativePath;
      }
      return relativePath;
    }
    exports2.maybeRelativePath = maybeRelativePath;
    var tsRe = /\.ts$/;
    var tsxRe = /\.tsx$/;
    var jsRe = /\.js$/;
    var jsxRe = /\.jsx$/;
    function maybeAdjustExtension(path2, importExtensions) {
      if (importExtensions === true) {
        return path2;
      } else if (importExtensions === false) {
        return path2.replace(extensionRegex, "");
      } else if (importExtensions === "js") {
        return path2.replace(tsRe, ".js").replace(tsxRe, ".jsx");
      } else if (importExtensions === "ts") {
        return path2.replace(jsRe, ".ts").replace(jsxRe, ".tsx");
      } else {
        throw new Error("Unsupported importExtensions value ${importExtensions}");
      }
    }
    var extensionRegex = /\.[tj]sx?/;
    function sameModule(path1, path2) {
      const [basePath1, basePath2] = [path1, path2].map((p) => p.replace(extensionRegex, ""));
      return basePath1 === basePath2 || path.resolve(basePath1) === path.resolve(basePath2);
    }
    exports2.sameModule = sameModule;
  }
});

// hardware/generic/goldfish/emulator/ui/aquarium/node_modules/ts-poet/build/is-plain-object.js
var require_is_plain_object = __commonJS({
  "hardware/generic/goldfish/emulator/ui/aquarium/node_modules/ts-poet/build/is-plain-object.js"(exports2) {
    "use strict";
    Object.defineProperty(exports2, "__esModule", { value: true });
    exports2.isPlainObject = void 0;
    function isPlainObject(o) {
      if (o === null || o === void 0)
        return false;
      if (!isObject(o))
        return false;
      const ctor = o.constructor;
      if (ctor === void 0)
        return true;
      if (!isObject(ctor.prototype))
        return false;
      if (!ctor.prototype.hasOwnProperty("isPrototypeOf"))
        return false;
      return true;
    }
    exports2.isPlainObject = isPlainObject;
    function isObject(o) {
      return Object.prototype.toString.call(o) === "[object Object]";
    }
  }
});

// hardware/generic/goldfish/emulator/ui/aquarium/node_modules/ts-poet/build/ConditionalOutput.js
var require_ConditionalOutput = __commonJS({
  "hardware/generic/goldfish/emulator/ui/aquarium/node_modules/ts-poet/build/ConditionalOutput.js"(exports2) {
    "use strict";
    Object.defineProperty(exports2, "__esModule", { value: true });
    exports2.MaybeOutput = exports2.ConditionalOutput = void 0;
    var Node_1 = require_Node();
    var ConditionalOutput = class extends Node_1.Node {
      // A given ConditionalOutput const could be used in multiple code
      // parents, and so we don't want to use instance state to store
      // "should I be output or not", b/c it depends on the containing tree.
      constructor(usageSiteName, declarationSiteCode, isType = false) {
        super();
        this.usageSiteName = usageSiteName;
        this.declarationSiteCode = declarationSiteCode;
        this.isType = isType;
      }
      /** Returns the declaration code, typically to be included near the bottom of your output as top-level scope. */
      get ifUsed() {
        return new MaybeOutput(this, this.declarationSiteCode);
      }
      get childNodes() {
        return [this.declarationSiteCode];
      }
      toCodeString() {
        return this.usageSiteName;
      }
    };
    exports2.ConditionalOutput = ConditionalOutput;
    var MaybeOutput = class {
      constructor(parent, code) {
        this.parent = parent;
        this.code = code;
      }
    };
    exports2.MaybeOutput = MaybeOutput;
  }
});

// hardware/google/aemu/protos/services_v2/tools/dprint_shim.js
var require_dprint_shim = __commonJS({
  "hardware/google/aemu/protos/services_v2/tools/dprint_shim.js"(exports2, module2) {
    module2.exports = { format: (_file, code, _options) => code };
  }
});

// hardware/generic/goldfish/emulator/ui/aquarium/node_modules/ts-poet/build/Code.js
var require_Code = __commonJS({
  "hardware/generic/goldfish/emulator/ui/aquarium/node_modules/ts-poet/build/Code.js"(exports2) {
    "use strict";
    var __importDefault = exports2 && exports2.__importDefault || function(mod) {
      return mod && mod.__esModule ? mod : { "default": mod };
    };
    Object.defineProperty(exports2, "__esModule", { value: true });
    exports2.Def = exports2.deepGenerate = exports2.Code = void 0;
    var Node_1 = require_Node();
    var Import_1 = require_Import();
    var is_plain_object_1 = require_is_plain_object();
    var ConditionalOutput_1 = require_ConditionalOutput();
    var index_1 = require_build();
    var dprint_node_1 = __importDefault(require_dprint_shim());
    var Code = class _Code extends Node_1.Node {
      constructor(literals, placeholders) {
        super();
        this.literals = literals;
        this.placeholders = placeholders;
        this.trim = false;
        this.oneline = false;
      }
      /** Returns the formatted code, with imports. */
      toString(opts = {}) {
        var _a;
        (_a = this.codeWithImports) !== null && _a !== void 0 ? _a : this.codeWithImports = this.generateCodeWithImports(opts);
        return opts.format === false ? this.codeWithImports : maybePretty(this.codeWithImports, opts.dprintOptions);
      }
      asOneline() {
        this.oneline = true;
        return this;
      }
      get childNodes() {
        return this.placeholders;
      }
      /**
       * Returns the unformatted, import-less code.
       *
       * This is an internal API, see `toString` for the public API.
       */
      toCodeString(used) {
        var _a;
        return (_a = this.code) !== null && _a !== void 0 ? _a : this.code = this.generateCode(used);
      }
      /**
       * Recursively collects all `ConditionalOutput` instances used within this `Code` block.
       */
      collectConditionalOutputs() {
        return this.placeholders.map((placeholder) => {
          if (placeholder instanceof ConditionalOutput_1.ConditionalOutput)
            return placeholder;
          if (placeholder instanceof _Code)
            return placeholder.collectConditionalOutputs();
          return null;
        }).flat().filter((val) => !!val);
      }
      deepFindAll(utilsUrl) {
        const used = [];
        const imports = [];
        const defs = [];
        const todo = [this];
        let i = 0;
        while (i < todo.length) {
          const placeholder = todo[i++];
          if (utilsUrl && placeholder instanceof ConditionalOutput_1.ConditionalOutput) {
            imports.push(Import_1.Import.importsName(placeholder.usageSiteName, utilsUrl, placeholder.isType));
            continue;
          }
          if (placeholder instanceof Node_1.Node) {
            todo.push(...placeholder.childNodes);
          } else if (Array.isArray(placeholder)) {
            todo.push(...placeholder);
          }
          if (placeholder instanceof ConditionalOutput_1.ConditionalOutput) {
            used.push(placeholder);
            todo.push(...placeholder.declarationSiteCode.childNodes);
          } else if (placeholder instanceof Import_1.Import) {
            imports.push(placeholder);
          } else if (placeholder instanceof Def) {
            defs.push(placeholder);
          } else if (placeholder instanceof ConditionalOutput_1.MaybeOutput) {
            if (used.includes(placeholder.parent)) {
              todo.push(placeholder.code);
            }
          }
        }
        return [used, imports, defs];
      }
      deepReplaceNamedImports(forceDefaultImport, forceModuleImport) {
        const assignedNames = {};
        function getName(source) {
          let name = assignedNames[source];
          if (!name) {
            name = `_m${Object.values(assignedNames).length}`;
            assignedNames[source] = name;
          }
          return name;
        }
        const todo = [this];
        let i = 0;
        while (i < todo.length) {
          const placeholder = todo[i++];
          if (placeholder instanceof Node_1.Node) {
            const array = placeholder.childNodes;
            for (let j = 0; j < array.length; j++) {
              const maybeImp = array[j];
              if (maybeImp instanceof Import_1.ImportsName && forceDefaultImport.includes(maybeImp.source)) {
                const name = getName(maybeImp.source);
                array[j] = (0, index_1.code)`${new Import_1.ImportsDefault(name, maybeImp.source)}.${maybeImp.sourceSymbol || maybeImp.symbol}`;
              } else if (maybeImp instanceof Import_1.ImportsName && forceModuleImport.includes(maybeImp.source)) {
                const name = getName(maybeImp.source);
                array[j] = (0, index_1.code)`${new Import_1.ImportsAll(name, maybeImp.source)}.${maybeImp.sourceSymbol || maybeImp.symbol}`;
              } else if (maybeImp instanceof Import_1.ImportsDefault && forceModuleImport.includes(maybeImp.source)) {
                array[j] = new Import_1.ImportsAll(maybeImp.symbol, maybeImp.source);
              }
            }
            todo.push(...placeholder.childNodes);
          } else if (Array.isArray(placeholder)) {
            todo.push(...placeholder);
          }
        }
      }
      generateCode(used) {
        const { literals, placeholders } = this;
        let result = "";
        for (let i = 0; i < placeholders.length; i++) {
          result += literals[i] + deepGenerate(used, placeholders[i]);
        }
        result += literals[literals.length - 1];
        if (this.trim) {
          result = result.trim();
        }
        if (this.oneline) {
          result = result.replace(/\n/g, "");
        }
        return result;
      }
      generateCodeWithImports(opts) {
        const { path = "", forceDefaultImport, forceModuleImport, forceRequireImport = [], importExtensions = true, prefix, importMappings = {} } = opts || {};
        const ourModulePath = path.replace(/\.[tj]sx?/, "");
        if (forceDefaultImport || forceModuleImport) {
          this.deepReplaceNamedImports(forceDefaultImport || [], forceModuleImport || []);
        }
        const [used, imports, defs] = this.deepFindAll(opts.conditionalUtils);
        assignAliasesIfNeeded(defs, imports, ourModulePath);
        const importPart = (0, Import_1.emitImports)(imports, ourModulePath, importMappings, forceRequireImport, importExtensions);
        const bodyPart = this.generateCode(used);
        const maybePrefix = prefix ? `${prefix}
` : "";
        return maybePrefix + importPart + "\n" + bodyPart;
      }
    };
    exports2.Code = Code;
    function deepGenerate(used, object) {
      let result = "";
      let todo = [object];
      let i = 0;
      while (i < todo.length) {
        const current = todo[i++];
        if (Array.isArray(current)) {
          todo.push(...current);
        } else if (current instanceof Node_1.Node) {
          result += current.toCodeString(used);
        } else if (current instanceof ConditionalOutput_1.MaybeOutput) {
          if (used.includes(current.parent)) {
            result += current.code.toCodeString(used);
          }
        } else if (current === null) {
          result += "null";
        } else if (current !== void 0) {
          if ((0, is_plain_object_1.isPlainObject)(current)) {
            result += JSON.stringify(current);
          } else {
            result += current.toString();
          }
        } else {
          result += "undefined";
        }
      }
      return result;
    }
    exports2.deepGenerate = deepGenerate;
    function assignAliasesIfNeeded(defs, imports, ourModulePath) {
      const usedSymbols = /* @__PURE__ */ new Set();
      defs.forEach((def) => usedSymbols.add(def.symbol));
      const assignedAliases = {};
      let j = 1;
      imports.forEach((i) => {
        if (i instanceof Import_1.ImportsName && // Don't both aliasing imports from our own module
        !((0, Import_1.sameModule)(i.source, ourModulePath) || i.definedIn && (0, Import_1.sameModule)(i.definedIn, ourModulePath))) {
          const key = `${i.symbol}@${i.source}`;
          if (usedSymbols.has(i.symbol)) {
            let alias = assignedAliases[key];
            if (!alias) {
              alias = `${i.symbol}${j++}`;
              assignedAliases[key] = alias;
            }
            if (alias !== i.symbol) {
              i.sourceSymbol = i.symbol;
            }
            i.symbol = alias;
          } else {
            usedSymbols.add(i.symbol);
            assignedAliases[key] = i.symbol;
          }
        }
      });
    }
    var baseOptions = {
      useTabs: false,
      useBraces: "always",
      singleBodyPosition: "nextLine",
      "arrowFunction.useParentheses": "force",
      // dprint-node uses `node: true`, which we want to undo
      "module.sortImportDeclarations": "caseSensitive",
      lineWidth: 120,
      // For some reason dprint seems to wrap lines "before it should" w/o this set (?)
      preferSingleLine: true
    };
    function maybePretty(input, options) {
      try {
        return dprint_node_1.default.format("file.ts", input.trim(), { ...baseOptions, ...options });
      } catch (e) {
        return input;
      }
    }
    var Def = class extends Node_1.Node {
      constructor(symbol) {
        super();
        this.symbol = symbol;
      }
      toCodeString() {
        return this.symbol;
      }
      /** Any potentially string/SymbolSpec/Code nested nodes within us. */
      get childNodes() {
        return [];
      }
    };
    exports2.Def = Def;
  }
});

// hardware/generic/goldfish/emulator/ui/aquarium/node_modules/ts-poet/build/Literal.js
var require_Literal = __commonJS({
  "hardware/generic/goldfish/emulator/ui/aquarium/node_modules/ts-poet/build/Literal.js"(exports2) {
    "use strict";
    Object.defineProperty(exports2, "__esModule", { value: true });
    exports2.Literal = void 0;
    var Node_1 = require_Node();
    var ConditionalOutput_1 = require_ConditionalOutput();
    var is_plain_object_1 = require_is_plain_object();
    var Literal = class extends Node_1.Node {
      constructor(object) {
        super();
        this.tokens = flatten(object);
      }
      get childNodes() {
        return this.tokens;
      }
      toCodeString(used) {
        return this.tokens.map((node) => {
          if (typeof node === "string")
            return node;
          if (node instanceof Node_1.Node)
            return node.toCodeString(used);
          return "";
        }).join(" ");
      }
    };
    exports2.Literal = Literal;
    function flatten(o) {
      if (typeof o === "undefined") {
        return ["undefined"];
      }
      if (typeof o === "object" && o != null) {
        if (o instanceof Node_1.Node || o instanceof ConditionalOutput_1.MaybeOutput) {
          return [o];
        } else if (Array.isArray(o)) {
          const nodes = ["["];
          for (let i = 0; i < o.length; i++) {
            if (i !== 0)
              nodes.push(",");
            nodes.push(...flatten(o[i]));
          }
          nodes.push("]");
          return nodes;
        } else if ((0, is_plain_object_1.isPlainObject)(o)) {
          const nodes = ["{"];
          const entries = Object.entries(o);
          for (let i = 0; i < entries.length; i++) {
            if (i !== 0)
              nodes.push(",");
            const [key, value] = entries[i];
            nodes.push(JSON.stringify(key), ":", ...flatten(value));
          }
          nodes.push("}");
          return nodes;
        }
      }
      return [JSON.stringify(o)];
    }
  }
});

// hardware/generic/goldfish/emulator/ui/aquarium/node_modules/ts-poet/build/saveFiles.js
var require_saveFiles = __commonJS({
  "hardware/generic/goldfish/emulator/ui/aquarium/node_modules/ts-poet/build/saveFiles.js"(exports2) {
    "use strict";
    Object.defineProperty(exports2, "__esModule", { value: true });
    exports2.saveFiles = void 0;
    var fs_1 = require("fs");
    var path_1 = require("path");
    var crypto_1 = require("crypto");
    async function saveFiles(opts) {
      const { toolName = "ts-poet", directory = "./", files, toStringOpts = {} } = opts;
      await Promise.all(files.map(async (file) => {
        const path = `${directory}/${file.name}`;
        await fs_1.promises.mkdir((0, path_1.dirname)(path), { recursive: true });
        const exists = await trueIfResolved(fs_1.promises.access(path));
        if (file.overwrite) {
          if (!file.hash) {
            await fs_1.promises.writeFile(path, contentToString(file, toStringOpts));
          } else {
            const hash = sha1(contentToString(file, { ...toStringOpts, format: false }));
            if (exists) {
              const existing = (await fs_1.promises.readFile(path)).toString();
              const match = existing.match(/\(hash=([0-9a-z]+)\.([0-9]+)\)/);
              if (match && match[1] === hash && String(existing.length) === match[2])
                return;
            }
            const formatted = contentToString(file, toStringOpts);
            const prefix = `// Generated by ${toolName} (hash=${hash}.`;
            const suffix = `)
` + formatted;
            const initialLength = prefix.length + suffix.length;
            const lengthLength = String(initialLength).length;
            const pad = lengthLength !== String(initialLength + lengthLength).length ? 1 : 0;
            await fs_1.promises.writeFile(path, prefix + String(initialLength + lengthLength + pad) + suffix);
          }
        } else if (!exists) {
          await fs_1.promises.writeFile(path, contentToString(file, toStringOpts));
        }
      }));
    }
    exports2.saveFiles = saveFiles;
    function contentToString(file, toStringOpts) {
      var _a;
      if (typeof file.contents === "string") {
        return file.contents;
      }
      return file.contents.toString({
        path: file.name,
        ...toStringOpts,
        ...file.toStringOpts,
        dprintOptions: {
          ...toStringOpts.dprintOptions,
          ...(_a = file.toStringOpts) === null || _a === void 0 ? void 0 : _a.dprintOptions
        }
      });
    }
    function sha1(content) {
      const sum = (0, crypto_1.createHash)("sha1");
      sum.update(content);
      return sum.digest("hex").substring(0, 6);
    }
    async function trueIfResolved(p) {
      return p.then(() => true, () => false);
    }
  }
});

// hardware/generic/goldfish/emulator/ui/aquarium/node_modules/ts-poet/build/index.js
var require_build = __commonJS({
  "hardware/generic/goldfish/emulator/ui/aquarium/node_modules/ts-poet/build/index.js"(exports2) {
    "use strict";
    Object.defineProperty(exports2, "__esModule", { value: true });
    exports2.generateConditionalsUtils = exports2.conditionalOutput = exports2.def = exports2.imp = exports2.joinCode = exports2.arrayOf = exports2.literalOf = exports2.code = exports2.saveFiles = exports2.Import = exports2.Code = void 0;
    var Import_1 = require_Import();
    var Code_1 = require_Code();
    var ConditionalOutput_1 = require_ConditionalOutput();
    var is_plain_object_1 = require_is_plain_object();
    var Literal_1 = require_Literal();
    var Code_2 = require_Code();
    Object.defineProperty(exports2, "Code", { enumerable: true, get: function() {
      return Code_2.Code;
    } });
    var Import_2 = require_Import();
    Object.defineProperty(exports2, "Import", { enumerable: true, get: function() {
      return Import_2.Import;
    } });
    var saveFiles_1 = require_saveFiles();
    Object.defineProperty(exports2, "saveFiles", { enumerable: true, get: function() {
      return saveFiles_1.saveFiles;
    } });
    function code(literals, ...placeholders) {
      return new Code_1.Code(literals, placeholders.map((p) => {
        if ((0, is_plain_object_1.isPlainObject)(p)) {
          return literalOf(p);
        } else {
          return p;
        }
      }));
    }
    exports2.code = code;
    function literalOf(object) {
      return new Literal_1.Literal(object);
    }
    exports2.literalOf = literalOf;
    function arrayOf(...elements) {
      return literalOf(elements);
    }
    exports2.arrayOf = arrayOf;
    function joinCode(chunks, opts = {}) {
      const { on = "", trim = true } = opts;
      const literals = [""];
      for (let i = 0; i < chunks.length - 1; i++) {
        literals.push(on);
      }
      literals.push("");
      if (trim) {
        chunks.forEach((c) => c.trim = true);
      }
      return new Code_1.Code(literals, chunks);
    }
    exports2.joinCode = joinCode;
    function imp(spec, opts = {}) {
      const sym = Import_1.Import.from(spec);
      if (opts && opts.definedIn) {
        sym.definedIn = opts.definedIn;
      }
      return sym;
    }
    exports2.imp = imp;
    function def(symbol) {
      return new Code_1.Def(symbol);
    }
    exports2.def = def;
    function conditionalOutput(usageSite, declarationSite, isType) {
      return new ConditionalOutput_1.ConditionalOutput(usageSite, declarationSite, isType);
    }
    exports2.conditionalOutput = conditionalOutput;
    function generateConditionalsUtils(used) {
      const mainChunk = joinCode([
        ...used.map(({ declarationSiteCode }) => declarationSiteCode)
      ], { on: "\n", trim: false });
      const uniqueOutputs = [...new Set(mainChunk.collectConditionalOutputs())].filter((output) => !used.includes(output));
      return joinCode([
        ...uniqueOutputs.map((output) => code`${output.ifUsed}`),
        mainChunk
      ], { on: "\n" });
    }
    exports2.generateConditionalsUtils = generateConditionalsUtils;
  }
});

// hardware/generic/goldfish/emulator/ui/aquarium/node_modules/ts-proto/build/src/options.js
var require_options = __commonJS({
  "hardware/generic/goldfish/emulator/ui/aquarium/node_modules/ts-proto/build/src/options.js"(exports2) {
    "use strict";
    Object.defineProperty(exports2, "__esModule", { value: true });
    exports2.OutputSchemaOption = exports2.ServiceOption = exports2.OneofOption = exports2.EnvOption = exports2.DurationOption = exports2.JsonTimestampOption = exports2.DateOption = exports2.LongOption = void 0;
    exports2.defaultOptions = defaultOptions;
    exports2.optionsFromParameter = optionsFromParameter;
    exports2.getTsPoetOpts = getTsPoetOpts;
    exports2.addTypeToMessages = addTypeToMessages;
    var LongOption;
    (function(LongOption2) {
      LongOption2["NUMBER"] = "number";
      LongOption2["LONG"] = "long";
      LongOption2["STRING"] = "string";
      LongOption2["BIGINT"] = "bigint";
    })(LongOption || (exports2.LongOption = LongOption = {}));
    var DateOption;
    (function(DateOption2) {
      DateOption2["DATE"] = "date";
      DateOption2["STRING"] = "string";
      DateOption2["STRING_NANO"] = "string-nano";
      DateOption2["TEMPORAL"] = "temporal";
      DateOption2["TIMESTAMP"] = "timestamp";
    })(DateOption || (exports2.DateOption = DateOption = {}));
    var JsonTimestampOption;
    (function(JsonTimestampOption2) {
      JsonTimestampOption2["RFC3339"] = "rfc3339";
      JsonTimestampOption2["RAW"] = "raw";
    })(JsonTimestampOption || (exports2.JsonTimestampOption = JsonTimestampOption = {}));
    var DurationOption;
    (function(DurationOption2) {
      DurationOption2["DURATION"] = "duration";
      DurationOption2["STRING"] = "string";
    })(DurationOption || (exports2.DurationOption = DurationOption = {}));
    var EnvOption;
    (function(EnvOption2) {
      EnvOption2["NODE"] = "node";
      EnvOption2["BROWSER"] = "browser";
      EnvOption2["BOTH"] = "both";
    })(EnvOption || (exports2.EnvOption = EnvOption = {}));
    var OneofOption;
    (function(OneofOption2) {
      OneofOption2["PROPERTIES"] = "properties";
      OneofOption2["UNIONS"] = "unions";
      OneofOption2["UNIONS_VALUE"] = "unions-value";
    })(OneofOption || (exports2.OneofOption = OneofOption = {}));
    var ServiceOption;
    (function(ServiceOption2) {
      ServiceOption2["GRPC"] = "grpc-js";
      ServiceOption2["NICE_GRPC"] = "nice-grpc";
      ServiceOption2["GENERIC"] = "generic-definitions";
      ServiceOption2["DEFAULT"] = "default";
      ServiceOption2["NONE"] = "none";
    })(ServiceOption || (exports2.ServiceOption = ServiceOption = {}));
    var OutputSchemaOption;
    (function(OutputSchemaOption2) {
      OutputSchemaOption2["TRUE"] = "true";
      OutputSchemaOption2["NO_FILE_DESCRIPTOR"] = "no-file-descriptor";
      OutputSchemaOption2["CONST"] = "const";
    })(OutputSchemaOption || (exports2.OutputSchemaOption = OutputSchemaOption = {}));
    function defaultOptions() {
      return {
        context: false,
        snakeToCamel: ["json", "keys"],
        protoJsonFormat: true,
        emitDefaultValues: [],
        globalThisPolyfill: false,
        forceLong: LongOption.NUMBER,
        useJsTypeOverride: false,
        useOptionals: "none",
        useDate: DateOption.DATE,
        useJsonTimestamp: JsonTimestampOption.RFC3339,
        useMongoObjectId: false,
        oneof: OneofOption.PROPERTIES,
        esModuleInterop: false,
        fileSuffix: "",
        importSuffix: "",
        lowerCaseServiceMethods: false,
        outputEncodeMethods: true,
        outputEncodeIncludeTypes: "",
        outputDecodeIncludeTypes: "",
        outputJsonMethods: true,
        outputPartialMethods: true,
        outputTypeAnnotations: false,
        outputTypeRegistry: false,
        stringEnums: false,
        constEnums: false,
        removeEnumPrefix: false,
        enumsAsLiterals: false,
        outputClientImpl: true,
        outputServices: [],
        returnObservable: false,
        addGrpcMetadata: false,
        metadataType: void 0,
        addNestjsRestParameter: false,
        nestJs: false,
        env: EnvOption.BOTH,
        unrecognizedEnum: true,
        unrecognizedEnumName: "UNRECOGNIZED",
        unrecognizedEnumValue: -1,
        exportCommonSymbols: true,
        outputSchema: false,
        onlyTypes: false,
        emitImportedFiles: true,
        useExactTypes: true,
        useAbortSignal: false,
        useAsyncIterable: false,
        unknownFields: false,
        usePrototypeForDefaults: false,
        useJsonName: false,
        useJsonWireFormat: false,
        useNumericEnumForJson: false,
        initializeFieldsAsUndefined: true,
        useMapType: false,
        useReadonlyTypes: false,
        useSnakeTypeName: true,
        outputExtensions: false,
        outputIndex: false,
        M: {},
        rpcBeforeRequest: false,
        rpcAfterResponse: false,
        rpcErrorHandler: false,
        comments: true,
        disableProto2Optionals: false,
        disableProto2DefaultValues: false,
        useNullAsOptional: false,
        annotateFilesWithVersion: true,
        noDefaultsForOptionals: false,
        bigIntLiteral: true,
        typePrefix: "",
        typeSuffix: "",
        useDuration: DurationOption.DURATION
      };
    }
    var nestJsOptions = {
      lowerCaseServiceMethods: true,
      outputEncodeMethods: false,
      outputJsonMethods: false,
      outputPartialMethods: false,
      outputClientImpl: false,
      useDate: DateOption.TIMESTAMP
    };
    function optionsFromParameter(parameter) {
      const options = defaultOptions();
      if (parameter) {
        const parsed = parseParameter(parameter);
        if (parsed.nestJs) {
          Object.assign(options, nestJsOptions);
        }
        Object.assign(options, parsed);
      }
      if (options.onlyTypes) {
        options.outputJsonMethods = false;
        options.outputEncodeMethods = false;
        options.outputClientImpl = false;
        options.nestJs = false;
      } else if (!options.outputJsonMethods && !options.outputEncodeMethods && !options.outputClientImpl && !options.nestJs) {
        options.onlyTypes = true;
      }
      if (options.forceLong === true) {
        options.forceLong = LongOption.LONG;
      }
      if (options.outputServices === false) {
        options.outputServices = [ServiceOption.NONE];
      }
      if (typeof options.outputServices == "string") {
        options.outputServices = [options.outputServices];
      }
      if (options.outputServices.length == 0 && !options.nestJs) {
        options.outputServices = [ServiceOption.DEFAULT];
      }
      if (options.nestJs && options.outputServices.length > 0) {
        options.outputEncodeMethods = true;
      }
      if (options.outputSchema === true) {
        options.outputSchema = [];
      }
      if (typeof options.outputSchema === "string") {
        options.outputSchema = [options.outputSchema];
      }
      if (options.useDate === true) {
        options.useDate = DateOption.DATE;
      } else if (options.useDate === false) {
        options.useDate = DateOption.TIMESTAMP;
      }
      if (options.snakeToCamel === false) {
        options.snakeToCamel = [];
      } else if (options.snakeToCamel === true) {
        options.snakeToCamel = ["keys", "json"];
      } else if (typeof options.snakeToCamel === "string") {
        options.snakeToCamel = options.snakeToCamel.split("_");
      }
      if (options.protoJsonFormat && !options.snakeToCamel.includes("json")) {
        options.snakeToCamel.push("json");
      }
      if (options.emitDefaultValues === "json-methods") {
        options.emitDefaultValues = ["json-methods"];
      } else {
        options.emitDefaultValues = [];
      }
      if (options.useJsonWireFormat) {
        if (!options.onlyTypes) {
          options.useJsonWireFormat = false;
        } else {
          options.stringEnums = true;
          options.useDate = DateOption.STRING;
        }
      }
      if (options.nestJs) {
        options.initializeFieldsAsUndefined = false;
      }
      if (options.outputIndex) {
        options.exportCommonSymbols = false;
      }
      if (options.rpcBeforeRequest || options.rpcAfterResponse || options.rpcErrorHandler) {
        const includesGeneric = options.outputServices.includes(ServiceOption.GENERIC);
        options.outputServices = [ServiceOption.DEFAULT];
        if (includesGeneric) {
          options.outputServices.push(ServiceOption.GENERIC);
        }
      }
      if (options.unrecognizedEnumValue) {
        options.unrecognizedEnumValue = Number(options.unrecognizedEnumValue);
      }
      return options;
    }
    function parseParameter(parameter) {
      const options = { M: {} };
      parameter.split(",").forEach((param) => {
        const optionSeparatorPos = param.indexOf("=");
        const key = param.substring(0, optionSeparatorPos);
        const value = parseParamValue(param.substring(optionSeparatorPos + 1));
        if (key.charAt(0) === "M") {
          if (typeof value !== "string") {
            console.warn(`ignoring invalid M option: '${param}'`);
          } else {
            const mKey = key.substring(1);
            if (options.M[mKey]) {
              console.warn(`received conflicting M options: '${param}' will override 'M${mKey}=${options.M[mKey]}'`);
            }
            if (param.endsWith(".ts")) {
              console.warn(`received M option '${param}' ending in '.ts' this is usually a mistake`);
            }
            options.M[mKey] = value;
          }
        } else if (options[key]) {
          options[key] = [options[key], value];
        } else {
          options[key] = value;
        }
      });
      return options;
    }
    function parseParamValue(value) {
      return value === "true" ? true : value === "false" ? false : value;
    }
    function getTsPoetOpts(options, tsProtoVersion, protocVersion, fileName) {
      const { importSuffix, esModuleInterop } = options;
      const pbjs = "protobufjs/minimal" + importSuffix;
      return {
        // Comment block at the top of every source file, since these comments require specific
        // syntax incompatible with ts-poet, we will hard-code the string and prepend to the
        // generator output.
        prefix: `// Code generated by protoc-gen-ts_proto. DO NOT EDIT.${options.annotateFilesWithVersion ? `
// versions:
//   protoc-gen-ts_proto  ${tsProtoVersion}
//   protoc               ${protocVersion}` : ""}
${fileName ? `// source: ${fileName}` : ""}

    /* eslint-disable */`,
        dprintOptions: { preferSingleLine: true, lineWidth: 120 },
        forceRequireImport: esModuleInterop ? [] : ["long"],
        forceDefaultImport: esModuleInterop ? [pbjs] : [],
        forceModuleImport: esModuleInterop ? [] : [pbjs]
      };
    }
    function addTypeToMessages(options) {
      return (options.outputTypeAnnotations || options.outputTypeRegistry) && options.outputTypeAnnotations !== "static-only";
    }
  }
});

// hardware/generic/goldfish/emulator/ui/aquarium/node_modules/case-anything/dist/cjs/index.cjs
var require_cjs = __commonJS({
  "hardware/generic/goldfish/emulator/ui/aquarium/node_modules/case-anything/dist/cjs/index.cjs"(exports2) {
    "use strict";
    var magicSplit = /^[a-zà-öø-ÿ]+|[A-ZÀ-ÖØ-ß][a-zà-öø-ÿ]+|[a-zà-öø-ÿ]+|[0-9]+|[A-ZÀ-ÖØ-ß]+(?![a-zà-öø-ÿ])/g;
    var spaceSplit = /\S+/g;
    function getPartsAndIndexes(string, splitRegex) {
      const result = { parts: [], prefixes: [] };
      const matches = string.matchAll(splitRegex);
      let lastWordEndIndex = 0;
      for (const match of matches) {
        if (typeof match.index !== "number")
          continue;
        const word = match[0];
        result.parts.push(word);
        const prefix = string.slice(lastWordEndIndex, match.index).trim();
        result.prefixes.push(prefix);
        lastWordEndIndex = match.index + word.length;
      }
      const tail = string.slice(lastWordEndIndex).trim();
      if (tail) {
        result.parts.push("");
        result.prefixes.push(tail);
      }
      return result;
    }
    function splitAndPrefix(string, options) {
      const { keepSpecialCharacters = false, keep, prefix = "" } = options || {};
      const normalString = string.trim().normalize("NFC");
      const hasSpaces = normalString.includes(" ");
      const split = hasSpaces ? spaceSplit : magicSplit;
      const partsAndIndexes = getPartsAndIndexes(normalString, split);
      return partsAndIndexes.parts.map((_part, i) => {
        let foundPrefix = partsAndIndexes.prefixes[i] || "";
        let part = _part;
        if (keepSpecialCharacters === false) {
          if (keep) {
            part = part.normalize("NFD").replace(new RegExp(`[^a-zA-Z\xD8\xDF\xF80-9${keep.join("")}]`, "g"), "");
          }
          if (!keep) {
            part = part.normalize("NFD").replace(/[^a-zA-ZØßø0-9]/g, "");
            foundPrefix = "";
          }
        }
        if (keep) {
          foundPrefix = foundPrefix.replace(new RegExp(`[^${keep.join("")}]`, "g"), "");
        }
        if (i === 0) {
          return foundPrefix + part;
        }
        if (!foundPrefix && !part)
          return "";
        if (!hasSpaces) {
          return (foundPrefix || prefix) + part;
        }
        if (!foundPrefix && prefix.match(/\s/)) {
          return " " + part;
        }
        return (foundPrefix || prefix) + part;
      }).filter(Boolean);
    }
    function capitaliseWord(string) {
      const match = string.matchAll(magicSplit).next().value;
      const firstLetterIndex = match ? match.index : 0;
      return string.slice(0, firstLetterIndex + 1).toUpperCase() + string.slice(firstLetterIndex + 1).toLowerCase();
    }
    function camelCase(string, options) {
      return splitAndPrefix(string, options).reduce((result, word, index) => {
        return index === 0 || !(word[0] || "").match(magicSplit) ? result + word.toLowerCase() : result + capitaliseWord(word);
      }, "");
    }
    function pascalCase(string, options) {
      return splitAndPrefix(string, options).reduce((result, word) => {
        return result + capitaliseWord(word);
      }, "");
    }
    var upperCamelCase = pascalCase;
    function kebabCase(string, options) {
      return splitAndPrefix(string, { ...options, prefix: "-" }).join("").toLowerCase();
    }
    function snakeCase(string, options) {
      return splitAndPrefix(string, { ...options, prefix: "_" }).join("").toLowerCase();
    }
    function constantCase(string, options) {
      return splitAndPrefix(string, { ...options, prefix: "_" }).join("").toUpperCase();
    }
    function trainCase(string, options) {
      return splitAndPrefix(string, { ...options, prefix: "-" }).map((word) => capitaliseWord(word)).join("");
    }
    function adaCase(string, options) {
      return splitAndPrefix(string, { ...options, prefix: "_" }).map((part) => capitaliseWord(part)).join("");
    }
    function cobolCase(string, options) {
      return splitAndPrefix(string, { ...options, prefix: "-" }).join("").toUpperCase();
    }
    function dotNotation(string, options) {
      return splitAndPrefix(string, { ...options, prefix: "." }).join("");
    }
    function pathCase(string, options = { keepSpecialCharacters: true }) {
      return splitAndPrefix(string, options).reduce((result, word, i) => {
        const prefix = i === 0 || word[0] === "/" ? "" : "/";
        return result + prefix + word;
      }, "");
    }
    function spaceCase(string, options = { keepSpecialCharacters: true }) {
      return splitAndPrefix(string, { ...options, prefix: " " }).join("");
    }
    function capitalCase(string, options = { keepSpecialCharacters: true }) {
      return splitAndPrefix(string, { ...options, prefix: " " }).reduce((result, word) => {
        return result + capitaliseWord(word);
      }, "");
    }
    function lowerCase(string, options = { keepSpecialCharacters: true }) {
      return splitAndPrefix(string, { ...options, prefix: " " }).join("").toLowerCase();
    }
    function upperCase(string, options = { keepSpecialCharacters: true }) {
      return splitAndPrefix(string, { ...options, prefix: " " }).join("").toUpperCase();
    }
    exports2.adaCase = adaCase;
    exports2.camelCase = camelCase;
    exports2.capitalCase = capitalCase;
    exports2.cobolCase = cobolCase;
    exports2.constantCase = constantCase;
    exports2.dotNotation = dotNotation;
    exports2.kebabCase = kebabCase;
    exports2.lowerCase = lowerCase;
    exports2.pascalCase = pascalCase;
    exports2.pathCase = pathCase;
    exports2.snakeCase = snakeCase;
    exports2.spaceCase = spaceCase;
    exports2.trainCase = trainCase;
    exports2.upperCamelCase = upperCamelCase;
    exports2.upperCase = upperCase;
  }
});

// hardware/generic/goldfish/emulator/ui/aquarium/node_modules/ts-proto/build/src/case.js
var require_case = __commonJS({
  "hardware/generic/goldfish/emulator/ui/aquarium/node_modules/ts-proto/build/src/case.js"(exports2) {
    "use strict";
    Object.defineProperty(exports2, "__esModule", { value: true });
    exports2.maybeSnakeToCamel = maybeSnakeToCamel;
    exports2.snakeToCamel = snakeToCamel;
    exports2.camelToSnake = camelToSnake;
    exports2.capitalize = capitalize;
    exports2.uncapitalize = uncapitalize;
    exports2.camelCaseGrpc = camelCaseGrpc;
    var case_anything_1 = require_cjs();
    function maybeSnakeToCamel(key, options) {
      if (options.snakeToCamel.includes("keys") && key.includes("_")) {
        return snakeToCamel(key);
      } else {
        return key;
      }
    }
    function snakeToCamel(s) {
      const hasLowerCase = !!s.match(/[a-z]/);
      return s.split("_").map((word, i) => {
        word = hasLowerCase ? word : word.toLowerCase();
        return i === 0 ? word : capitalize(word);
      }).join("");
    }
    function camelToSnake(s) {
      return s.replace(/[a-z0-9]([A-Z])/g, (m) => m[0] + "_" + m[1]).replace(/[A-Z]([A-Z][a-z])/g, (m) => m[0] + "_" + m.substring(1)).toUpperCase();
    }
    function capitalize(s) {
      return s.substring(0, 1).toUpperCase() + s.substring(1);
    }
    function uncapitalize(s) {
      return s.substring(0, 1).toLowerCase() + s.substring(1);
    }
    function camelCaseGrpc(s) {
      return (0, case_anything_1.camelCase)(s);
    }
  }
});

// hardware/generic/goldfish/emulator/ui/aquarium/node_modules/ts-proto/build/src/utils.js
var require_utils2 = __commonJS({
  "hardware/generic/goldfish/emulator/ui/aquarium/node_modules/ts-proto/build/src/utils.js"(exports2) {
    "use strict";
    Object.defineProperty(exports2, "__esModule", { value: true });
    exports2.FormattedMethodDescriptor = void 0;
    exports2.protoFilesToGenerate = protoFilesToGenerate;
    exports2.generateIndexFiles = generateIndexFiles;
    exports2.readToBuffer = readToBuffer;
    exports2.fail = fail;
    exports2.singular = singular;
    exports2.lowerFirst = lowerFirst;
    exports2.upperFirst = upperFirst;
    exports2.maybeAddComment = maybeAddComment;
    exports2.maybePrefixPackage = maybePrefixPackage;
    exports2.assertInstanceOf = assertInstanceOf;
    exports2.getFieldJsonName = getFieldJsonName;
    exports2.getFieldName = getFieldName;
    exports2.safeAccessor = safeAccessor;
    exports2.getPropertyAccessor = getPropertyAccessor;
    exports2.impFile = impFile;
    exports2.impProto = impProto;
    exports2.tryCatchBlock = tryCatchBlock;
    exports2.arrowFunction = arrowFunction;
    exports2.nullOrUndefined = nullOrUndefined;
    exports2.maybeCheckIsNotNull = maybeCheckIsNotNull;
    exports2.maybeCheckIsNull = maybeCheckIsNull;
    exports2.oneofValueName = oneofValueName;
    exports2.withOrMaybeCheckIsNotNull = withOrMaybeCheckIsNotNull;
    exports2.withOrMaybeCheckIsNull = withOrMaybeCheckIsNull;
    exports2.withAndMaybeCheckIsNotNull = withAndMaybeCheckIsNotNull;
    exports2.withAndMaybeCheckIsNull = withAndMaybeCheckIsNull;
    exports2.getVersions = getVersions;
    exports2.wrapTypeName = wrapTypeName;
    var path = require("path");
    var ts_poet_1 = require_build();
    var options_12 = require_options();
    var case_1 = require_case();
    function protoFilesToGenerate(request) {
      return request.protoFile.filter((f) => request.fileToGenerate.includes(f.name));
    }
    function generateIndexFiles(files, options) {
      const packageTree = {
        index: "index.ts",
        leaves: {},
        chunks: []
      };
      for (const { name, package: pkg } of files) {
        const moduleName = name.replace(".proto", options.fileSuffix);
        const pkgParts = pkg.length > 0 ? pkg.split(".") : [];
        const branch = pkgParts.reduce((branch2, part, i) => {
          if (!(part in branch2.leaves)) {
            const prePkgParts = pkgParts.slice(0, i + 1);
            const index = `index.${prePkgParts.join(".")}.ts`;
            branch2.chunks.push((0, ts_poet_1.code)`export * as ${part} from "./${path.basename(index, ".ts") + options.importSuffix}";`);
            branch2.leaves[part] = {
              index,
              leaves: {},
              chunks: []
            };
          }
          return branch2.leaves[part];
        }, packageTree);
        branch.chunks.push((0, ts_poet_1.code)`export * from "./${moduleName + options.importSuffix}";`);
      }
      const indexFiles = [];
      let branches = [packageTree];
      let currentBranch;
      while (currentBranch = branches.pop()) {
        indexFiles.push([currentBranch.index, (0, ts_poet_1.joinCode)(currentBranch.chunks)]);
        branches.push(...Object.values(currentBranch.leaves));
      }
      return indexFiles;
    }
    function readToBuffer(stream) {
      return new Promise((resolve) => {
        const ret = [];
        let len = 0;
        stream.on("readable", () => {
          let chunk;
          while (chunk = stream.read()) {
            ret.push(chunk);
            len += chunk.length;
          }
        });
        stream.on("end", () => {
          resolve(Buffer.concat(ret, len));
        });
      });
    }
    function fail(message) {
      throw new Error(message);
    }
    function singular(name) {
      return name.substring(0, name.length - 1);
    }
    function lowerFirst(name) {
      return name.substring(0, 1).toLowerCase() + name.substring(1);
    }
    function upperFirst(name) {
      return name.substring(0, 1).toUpperCase() + name.substring(1);
    }
    var CloseComment = /\*\//g;
    function maybeAddComment(options, desc, chunks, deprecated, prefix = "") {
      if (!options.comments) {
        return;
      }
      let lines = [];
      if (desc.leadingComments || desc.trailingComments) {
        let content = (desc.leadingComments || desc.trailingComments || "").replace(CloseComment, "* /").trim();
        const isDoubleStar = content.startsWith("*");
        if (isDoubleStar) {
          content = content.substring(1).trim();
        }
        if (prefix) {
          content = prefix + content;
        }
        lines = content.split("\n").map((l) => l.replace(/^ /, "").replace(/\n/, ""));
      }
      if (deprecated) {
        if (lines.length > 0) {
          lines.push("");
        }
        lines.push("@deprecated");
      }
      let comment;
      if (lines.length === 1) {
        comment = (0, ts_poet_1.code)`/** ${lines[0]} */`;
      } else {
        comment = (0, ts_poet_1.code)`/**\n * ${lines.join("\n * ")}\n */`;
      }
      if (lines.length > 0) {
        chunks.push((0, ts_poet_1.code)`\n\n${comment}\n\n`);
      }
    }
    function maybePrefixPackage(fileDesc, rest) {
      const prefix = fileDesc.package === "" ? "" : `${fileDesc.package}.`;
      return `${prefix}${rest}`;
    }
    function assertInstanceOf(obj, constructor) {
      if (!(obj instanceof constructor)) {
        throw new Error(`Expected instance of ${constructor.name}`);
      }
    }
    var FormattedMethodDescriptor = class _FormattedMethodDescriptor {
      /**
       * The name of this method with formatting applied according to the `Options` object passed to the constructor.
       * Automatically updates to any changes to the `Options` or `name` of this object
       */
      get formattedName() {
        return _FormattedMethodDescriptor.formatName(this.name, this.ctxOptions);
      }
      constructor(src, options) {
        this.ctxOptions = options;
        this.original = src;
        this.name = src.name;
        this.inputType = src.inputType;
        this.outputType = src.outputType;
        this.options = src.options;
        this.clientStreaming = src.clientStreaming;
        this.serverStreaming = src.serverStreaming;
      }
      /**
       * Retrieve the source `MethodDescriptorProto` used to construct this object
       * @returns The source `MethodDescriptorProto` used to construct this object
       */
      getSource() {
        return this.original;
      }
      /**
       * Applies formatting rules to a gRPC method name.
       * @param methodName The original method name
       * @param options The options object containing rules to apply
       * @returns The formatted method name
       */
      static formatName(methodName, options) {
        let result = methodName;
        if (options.lowerCaseServiceMethods || options.outputServices.includes(options_12.ServiceOption.GRPC)) {
          if (options.snakeToCamel)
            result = (0, case_1.camelCaseGrpc)(result);
        }
        return result;
      }
    };
    exports2.FormattedMethodDescriptor = FormattedMethodDescriptor;
    function getFieldJsonName(field, options) {
      if (options.useJsonName) {
        return field.jsonName;
      }
      if (options.snakeToCamel.includes("json")) {
        return field.jsonName;
      } else {
        const probableJsonName = (0, case_1.snakeToCamel)(field.name);
        const isJsonNameSet = probableJsonName !== field.jsonName;
        return isJsonNameSet ? field.jsonName : field.name;
      }
    }
    function getFieldName(field, options) {
      if (options.useJsonName) {
        return field.jsonName;
      }
      return (0, case_1.maybeSnakeToCamel)(field.name, options);
    }
    function isValidIdentifier(propertyName) {
      return /^[a-zA-Z_$][\w$]*$/.test(propertyName);
    }
    function safeAccessor(propertyName) {
      return isValidIdentifier(propertyName) ? propertyName : JSON.stringify(propertyName);
    }
    function getPropertyAccessor(objectName, propertyName, optional = false) {
      return isValidIdentifier(propertyName) ? `${objectName}${optional ? "?" : ""}.${propertyName}` : `${objectName}${optional ? "?." : ""}[${safeAccessor(propertyName)}]`;
    }
    function impFile(options, spec) {
      return (0, ts_poet_1.imp)(`${spec}${options.importSuffix}`);
    }
    function impProto(options, module3, type) {
      const prefix = options.onlyTypes ? "t:" : "";
      const protoFile = `${module3}.proto`;
      if (options.M[protoFile]) {
        return (0, ts_poet_1.imp)(`${prefix}${type}@${options.M[protoFile]}`);
      }
      return (0, ts_poet_1.imp)(`${prefix}${type}@./${module3}${options.fileSuffix}${options.importSuffix}`);
    }
    function tryCatchBlock(tryBlock, handleErrorBlock) {
      return (0, ts_poet_1.code)`try {
    ${tryBlock}
  } catch (error) {
    ${handleErrorBlock}
  }`;
    }
    function arrowFunction(params, body, isOneLine = true) {
      if (isOneLine) {
        return (0, ts_poet_1.code)`(${params}) => ${body}`;
      }
      return (0, ts_poet_1.code)`(${params}) => { ${body} }`;
    }
    function nullOrUndefined(options, hasProto3Optional = false) {
      return options.useNullAsOptional ? `null ${hasProto3Optional ? "| undefined" : ""}` : "undefined";
    }
    function maybeCheckIsNotNull(options, typeName, prefix) {
      return options.useNullAsOptional ? ` ${prefix} ${typeName} !== null` : "";
    }
    function maybeCheckIsNull(options, typeName, prefix) {
      return options.useNullAsOptional ? ` ${prefix} ${typeName} === null` : "";
    }
    function oneofValueName(fieldName, options) {
      return options.oneof === options_12.OneofOption.UNIONS ? fieldName : "value";
    }
    function withOrMaybeCheckIsNotNull(options, typeName) {
      return maybeCheckIsNotNull(options, typeName, "||");
    }
    function withOrMaybeCheckIsNull(options, typeName) {
      return maybeCheckIsNull(options, typeName, "||");
    }
    function withAndMaybeCheckIsNotNull(options, typeName) {
      return maybeCheckIsNotNull(options, typeName, "&&");
    }
    function withAndMaybeCheckIsNull(options, typeName) {
      return maybeCheckIsNotNull(options, typeName, "&&");
    }
    async function getVersions(request) {
      let protocVersion = "unknown";
      if (request.compilerVersion) {
        const { major, minor, patch } = request.compilerVersion;
        protocVersion = `v${major}.${minor}.${patch}`;
      }
      const packageJson = await readPackageJson();
      const tsProtoVersion = `v${packageJson?.version ?? "unknown"}`;
      return { protocVersion, tsProtoVersion };
    }
    async function readPackageJson() {
      return { version: "2.6.1" };
    }
    function wrapTypeName(options, name) {
      return options.typePrefix + name + options.typeSuffix;
    }
  }
});

// hardware/generic/goldfish/emulator/ui/aquarium/node_modules/ts-proto/build/src/sourceInfo.js
var require_sourceInfo = __commonJS({
  "hardware/generic/goldfish/emulator/ui/aquarium/node_modules/ts-proto/build/src/sourceInfo.js"(exports2) {
    "use strict";
    Object.defineProperty(exports2, "__esModule", { value: true });
    exports2.Fields = void 0;
    exports2.Fields = {
      file: {
        syntax: 12,
        message_type: 4,
        enum_type: 5,
        service: 6,
        extension: 7
      },
      message: {
        field: 2,
        nested_type: 3,
        enum_type: 4,
        oneof_decl: 8
      },
      enum: {
        value: 2
      },
      service: {
        method: 2
      }
    };
    var EmptyDescription = class {
      constructor() {
        this.span = [];
        this.leadingComments = "";
        this.trailingComments = "";
        this.leadingDetachedComments = [];
      }
    };
    var SourceInfo = class _SourceInfo {
      /** Returns an empty SourceInfo */
      static empty() {
        return new _SourceInfo({}, new EmptyDescription());
      }
      /**
       * Creates the SourceInfo from the FileDescriptorProto given to you
       * by the protoc compiler. It indexes file.sourceCodeInfo by dotted
       * path notation and returns the root SourceInfo.
       */
      static fromDescriptor(file) {
        let map = {};
        if (file.sourceCodeInfo && file.sourceCodeInfo.location) {
          file.sourceCodeInfo.location.forEach((loc) => {
            map[loc.path.join(".")] = loc;
          });
        }
        return new _SourceInfo(map, new EmptyDescription());
      }
      // Private
      constructor(sourceCode, selfDescription) {
        this.sourceCode = sourceCode;
        this.selfDescription = selfDescription;
      }
      /** Returns the code span [start line, start column, end line] */
      get span() {
        return this.selfDescription.span;
      }
      /** Leading consecutive comment lines prior to the current element */
      get leadingComments() {
        return this.selfDescription.leadingComments;
      }
      /** Documentation is unclear about what exactly this is */
      get trailingComments() {
        return this.selfDescription.trailingComments;
      }
      /** Detached comments are those preceding but separated by a blank non-comment line */
      get leadingDetachedComments() {
        return this.selfDescription.leadingDetachedComments;
      }
      /** Return the source info for the field id and index specified */
      lookup(type, index) {
        if (index === void 0) {
          return this.sourceCode[`${type}`] || new EmptyDescription();
        }
        return this.sourceCode[`${type}.${index}`] || new EmptyDescription();
      }
      /** Returns a new SourceInfo class representing the field id and index specified */
      open(type, index) {
        const prefix = `${type}.${index}.`;
        const map = {};
        Object.keys(this.sourceCode).filter((key) => key.startsWith(prefix)).forEach((key) => {
          map[key.substr(prefix.length)] = this.sourceCode[key];
        });
        return new _SourceInfo(map, this.lookup(type, index));
      }
    };
    exports2.default = SourceInfo;
  }
});

// hardware/generic/goldfish/emulator/ui/aquarium/node_modules/ts-proto/build/src/enums.js
var require_enums = __commonJS({
  "hardware/generic/goldfish/emulator/ui/aquarium/node_modules/ts-proto/build/src/enums.js"(exports2) {
    "use strict";
    Object.defineProperty(exports2, "__esModule", { value: true });
    exports2.generateEnum = generateEnum;
    exports2.generateEnumFromJson = generateEnumFromJson;
    exports2.generateEnumToJson = generateEnumToJson;
    exports2.generateEnumToNumber = generateEnumToNumber;
    exports2.getMemberName = getMemberName;
    var ts_poet_1 = require_build();
    var utils_12 = require_utils2();
    var case_1 = require_case();
    var sourceInfo_1 = require_sourceInfo();
    function generateEnum(ctx, fullName, enumDesc, sourceInfo) {
      const { options } = ctx;
      const chunks = [];
      let unrecognizedEnum = { present: false };
      (0, utils_12.maybeAddComment)(options, sourceInfo, chunks, enumDesc.options?.deprecated);
      if (options.enumsAsLiterals) {
        chunks.push((0, ts_poet_1.code)`export const ${(0, ts_poet_1.def)(fullName)} = {`);
      } else {
        chunks.push((0, ts_poet_1.code)`export ${options.constEnums ? "const " : ""}enum ${(0, ts_poet_1.def)(fullName)} {`);
      }
      const delimiter = options.enumsAsLiterals ? ":" : "=";
      enumDesc.value.forEach((valueDesc, index) => {
        const info = sourceInfo.lookup(sourceInfo_1.Fields.enum.value, index);
        const valueName = getValueName(ctx, fullName, valueDesc);
        const memberName = getMemberName(ctx, enumDesc, valueDesc);
        if (valueDesc.number === options.unrecognizedEnumValue) {
          unrecognizedEnum = { present: true, name: memberName, originalName: valueName };
        }
        (0, utils_12.maybeAddComment)(options, info, chunks, valueDesc.options?.deprecated, `${memberName} - `);
        chunks.push((0, ts_poet_1.code)`${memberName} ${delimiter} ${options.stringEnums ? `"${valueName}"` : valueDesc.number.toString()},`);
      });
      if (options.unrecognizedEnum && !unrecognizedEnum.present) {
        chunks.push((0, ts_poet_1.code)`
      ${options.unrecognizedEnumName} ${delimiter} ${options.stringEnums ? `"${options.unrecognizedEnumName}"` : options.unrecognizedEnumValue.toString()},`);
      }
      if (options.enumsAsLiterals) {
        chunks.push((0, ts_poet_1.code)`} as const`);
        chunks.push((0, ts_poet_1.code)`\n`);
        chunks.push((0, ts_poet_1.code)`export type ${(0, ts_poet_1.def)(fullName)} = typeof ${(0, ts_poet_1.def)(fullName)}[keyof typeof ${(0, ts_poet_1.def)(fullName)}]`);
        chunks.push((0, ts_poet_1.code)`\n`);
        chunks.push((0, ts_poet_1.code)`export namespace ${(0, ts_poet_1.def)(fullName)} {`);
        enumDesc.value.forEach((valueDesc) => {
          const memberName = getMemberName(ctx, enumDesc, valueDesc);
          chunks.push((0, ts_poet_1.code)`export type ${memberName} = typeof ${(0, ts_poet_1.def)(fullName)}.${memberName};`);
        });
        if (options.unrecognizedEnum && !unrecognizedEnum.present) {
          chunks.push((0, ts_poet_1.code)`export type ${options.unrecognizedEnumName} = typeof ${(0, ts_poet_1.def)(fullName)}.${options.unrecognizedEnumName};`);
        }
        chunks.push((0, ts_poet_1.code)`}`);
      } else {
        chunks.push((0, ts_poet_1.code)`}`);
      }
      if (options.outputJsonMethods === true || options.outputJsonMethods === "from-only" || options.stringEnums && options.outputEncodeMethods) {
        chunks.push((0, ts_poet_1.code)`\n`);
        chunks.push(generateEnumFromJson(ctx, fullName, enumDesc, unrecognizedEnum));
      }
      if (options.outputJsonMethods === true || options.outputJsonMethods === "to-only") {
        chunks.push((0, ts_poet_1.code)`\n`);
        chunks.push(generateEnumToJson(ctx, fullName, enumDesc, unrecognizedEnum));
      }
      if (options.stringEnums && options.outputEncodeMethods) {
        chunks.push((0, ts_poet_1.code)`\n`);
        chunks.push(generateEnumToNumber(ctx, fullName, enumDesc, unrecognizedEnum));
      }
      return (0, ts_poet_1.joinCode)(chunks, { on: "\n" });
    }
    function generateEnumFromJson(ctx, fullName, enumDesc, unrecognizedEnum) {
      const { options, utils } = ctx;
      const chunks = [];
      const functionName = (0, case_1.uncapitalize)(fullName) + "FromJSON";
      chunks.push((0, ts_poet_1.code)`export function ${(0, ts_poet_1.def)(functionName)}(object: any): ${fullName} {`);
      chunks.push((0, ts_poet_1.code)`switch (object) {`);
      for (const valueDesc of enumDesc.value) {
        const memberName = getMemberName(ctx, enumDesc, valueDesc);
        const valueName = getValueName(ctx, fullName, valueDesc);
        chunks.push((0, ts_poet_1.code)`
      case ${valueDesc.number}:
      case "${valueName}":
        return ${fullName}.${memberName};
    `);
      }
      if (options.unrecognizedEnum) {
        if (!unrecognizedEnum.present) {
          chunks.push((0, ts_poet_1.code)`
        case ${options.unrecognizedEnumValue}:
        case "${options.unrecognizedEnumName}":
        default:
          return ${fullName}.${options.unrecognizedEnumName};
      `);
        } else {
          chunks.push((0, ts_poet_1.code)`
        default:
          return ${fullName}.${unrecognizedEnum.name};
      `);
        }
      } else {
        chunks.push((0, ts_poet_1.code)`
      default:
        throw new ${utils.globalThis}.Error("Unrecognized enum value " + object + " for enum ${fullName}");
    `);
      }
      chunks.push((0, ts_poet_1.code)`}`);
      chunks.push((0, ts_poet_1.code)`}`);
      return (0, ts_poet_1.joinCode)(chunks, { on: "\n" });
    }
    function generateEnumToJson(ctx, fullName, enumDesc, unrecognizedEnum) {
      const { options, utils } = ctx;
      const chunks = [];
      const functionName = (0, case_1.uncapitalize)(fullName) + "ToJSON";
      chunks.push((0, ts_poet_1.code)`export function ${(0, ts_poet_1.def)(functionName)}(object: ${fullName}): ${ctx.options.useNumericEnumForJson ? "number" : "string"} {`);
      chunks.push((0, ts_poet_1.code)`switch (object) {`);
      for (const valueDesc of enumDesc.value) {
        if (ctx.options.useNumericEnumForJson) {
          const memberName = getMemberName(ctx, enumDesc, valueDesc);
          chunks.push((0, ts_poet_1.code)`case ${fullName}.${memberName}: return ${valueDesc.number};`);
        } else {
          const memberName = getMemberName(ctx, enumDesc, valueDesc);
          const valueName = getValueName(ctx, fullName, valueDesc);
          chunks.push((0, ts_poet_1.code)`case ${fullName}.${memberName}: return "${valueName}";`);
        }
      }
      if (options.unrecognizedEnum) {
        if (!unrecognizedEnum.present) {
          chunks.push((0, ts_poet_1.code)`
        case ${fullName}.${options.unrecognizedEnumName}:`);
          if (ctx.options.useNumericEnumForJson) {
            chunks.push((0, ts_poet_1.code)`
        default:
          return ${options.unrecognizedEnumValue};
      `);
          } else {
            chunks.push((0, ts_poet_1.code)`
        default:
          return "${options.unrecognizedEnumName}";
      `);
          }
        } else if (ctx.options.useNumericEnumForJson) {
          chunks.push((0, ts_poet_1.code)`
        default:
          return ${options.unrecognizedEnumValue};
      `);
        } else {
          chunks.push((0, ts_poet_1.code)`
      default:
        return "${unrecognizedEnum.originalName}";
    `);
        }
      } else {
        chunks.push((0, ts_poet_1.code)`
      default:
        throw new ${utils.globalThis}.Error("Unrecognized enum value " + object + " for enum ${fullName}");
    `);
      }
      chunks.push((0, ts_poet_1.code)`}`);
      chunks.push((0, ts_poet_1.code)`}`);
      return (0, ts_poet_1.joinCode)(chunks, { on: "\n" });
    }
    function generateEnumToNumber(ctx, fullName, enumDesc, unrecognizedEnum) {
      const { options, utils } = ctx;
      const chunks = [];
      const functionName = (0, case_1.uncapitalize)(fullName) + "ToNumber";
      chunks.push((0, ts_poet_1.code)`export function ${(0, ts_poet_1.def)(functionName)}(object: ${fullName}): number {`);
      chunks.push((0, ts_poet_1.code)`switch (object) {`);
      for (const valueDesc of enumDesc.value) {
        chunks.push((0, ts_poet_1.code)`case ${fullName}.${getMemberName(ctx, enumDesc, valueDesc)}: return ${valueDesc.number};`);
      }
      if (options.unrecognizedEnum) {
        if (!unrecognizedEnum.present) {
          chunks.push((0, ts_poet_1.code)`
        case ${fullName}.${options.unrecognizedEnumName}:
        default:
          return ${options.unrecognizedEnumValue};
      `);
        } else {
          chunks.push((0, ts_poet_1.code)`
        default:
          return ${options.unrecognizedEnumValue};
      `);
        }
      } else {
        chunks.push((0, ts_poet_1.code)`
      default:
        throw new ${utils.globalThis}.Error("Unrecognized enum value " + object + " for enum ${fullName}");
    `);
      }
      chunks.push((0, ts_poet_1.code)`}`);
      chunks.push((0, ts_poet_1.code)`}`);
      return (0, ts_poet_1.joinCode)(chunks, { on: "\n" });
    }
    var withoutEnumPrefix = (valueName, enumName) => valueName.replace(`${(0, case_1.camelToSnake)(enumName)}_`, "");
    var isNumeric = (str) => !isNaN(Number(str));
    function getMemberName(ctx, enumDesc, valueDesc) {
      const areAnyMembersNumericWithoutPrefix = enumDesc.value.some((v) => {
        const withoutPrefix = withoutEnumPrefix(v.name, enumDesc.name);
        return isNumeric(withoutPrefix) || isNumeric(withoutPrefix[0]);
      });
      const nameWithoutPrefix = withoutEnumPrefix(valueDesc.name, enumDesc.name);
      if (ctx.options.removeEnumPrefix && !areAnyMembersNumericWithoutPrefix) {
        return nameWithoutPrefix;
      }
      return valueDesc.name;
    }
    function getValueName(ctx, fullName, valueDesc) {
      return valueDesc.name;
    }
  }
});

// hardware/generic/goldfish/emulator/ui/aquarium/node_modules/ts-proto/build/src/generate-async-iterable.js
var require_generate_async_iterable = __commonJS({
  "hardware/generic/goldfish/emulator/ui/aquarium/node_modules/ts-proto/build/src/generate-async-iterable.js"(exports2) {
    "use strict";
    Object.defineProperty(exports2, "__esModule", { value: true });
    exports2.generateEncodeTransform = generateEncodeTransform;
    exports2.generateDecodeTransform = generateDecodeTransform;
    var ts_poet_1 = require_build();
    function generateEncodeTransform(utils, fullName) {
      return (0, ts_poet_1.code)`
    // encodeTransform encodes a source of message objects.
    // Transform<${fullName}, Uint8Array>
    async *encodeTransform(
      source: AsyncIterable<${fullName} | ${fullName}[]> | Iterable<${fullName} | ${fullName}[]>
    ): AsyncIterable<Uint8Array> {
      for await (const pkt of source) {
        if (${utils.globalThis}.Array.isArray(pkt)) {
          for (const p of (pkt as any)) {
            yield* [${fullName}.encode(p).finish()]
          }
        } else {
          yield* [${fullName}.encode(pkt as any).finish()]
        }
      }
    }
  `;
    }
    function generateDecodeTransform(utils, fullName) {
      return (0, ts_poet_1.code)`
    // decodeTransform decodes a source of encoded messages.
    // Transform<Uint8Array, ${fullName}>
    async *decodeTransform(
      source: AsyncIterable<Uint8Array | Uint8Array[]> | Iterable<Uint8Array | Uint8Array[]>
    ): AsyncIterable<${fullName}> {
      for await (const pkt of source) {
        if (${utils.globalThis}.Array.isArray(pkt)) {
          for (const p of (pkt as any)) {
            yield* [${fullName}.decode(p)]
          }
        } else {
          yield* [${fullName}.decode(pkt as any)]
        }
      }
    }
  `;
    }
  }
});

// hardware/generic/goldfish/emulator/ui/aquarium/node_modules/ts-proto/build/src/visit.js
var require_visit = __commonJS({
  "hardware/generic/goldfish/emulator/ui/aquarium/node_modules/ts-proto/build/src/visit.js"(exports2) {
    "use strict";
    Object.defineProperty(exports2, "__esModule", { value: true });
    exports2.visit = visit;
    exports2.visitServices = visitServices;
    var utils_12 = require_utils2();
    var sourceInfo_1 = require_sourceInfo();
    var case_1 = require_case();
    function visit(proto, sourceInfo, messageFn, options, enumFn = () => {
    }, tsPrefix = "", protoPrefix = "") {
      const isRootFile = "syntax" in proto;
      const childEnumType = isRootFile ? sourceInfo_1.Fields.file.enum_type : sourceInfo_1.Fields.message.enum_type;
      proto.enumType.forEach((enumDesc, index) => {
        const protoFullName = protoPrefix + enumDesc.name;
        const tsFullName = tsPrefix + (0, case_1.maybeSnakeToCamel)(enumDesc.name, options);
        const tsFullNameWithAffixes = messageName((0, utils_12.wrapTypeName)(options, tsFullName));
        const nestedSourceInfo = sourceInfo.open(childEnumType, index);
        enumFn(tsFullNameWithAffixes, enumDesc, nestedSourceInfo, protoFullName);
      });
      const messages = "messageType" in proto ? proto.messageType : proto.nestedType;
      const childType = isRootFile ? sourceInfo_1.Fields.file.message_type : sourceInfo_1.Fields.message.nested_type;
      messages.forEach((message, index) => {
        const protoFullName = protoPrefix + message.name;
        const tsFullName = tsPrefix + (0, case_1.maybeSnakeToCamel)(message.name, options);
        const tsFullNameWithAffixes = messageName((0, utils_12.wrapTypeName)(options, tsFullName));
        const nestedSourceInfo = sourceInfo.open(childType, index);
        messageFn(tsFullNameWithAffixes, message, nestedSourceInfo, protoFullName);
        const delim = options.useSnakeTypeName ? "_" : "";
        visit(message, nestedSourceInfo, messageFn, options, enumFn, tsFullName + delim, protoFullName + ".");
      });
    }
    var builtInNames = ["Date", "Function"];
    function messageName(name) {
      return builtInNames.includes(name) ? `${name}Message` : name;
    }
    function visitServices(proto, sourceInfo, serviceFn) {
      proto.service.forEach((serviceDesc, index) => {
        const nestedSourceInfo = sourceInfo.open(sourceInfo_1.Fields.file.service, index);
        serviceFn(serviceDesc, nestedSourceInfo);
      });
    }
  }
});

// hardware/generic/goldfish/emulator/ui/aquarium/node_modules/ts-proto/build/src/types.js
var require_types = __commonJS({
  "hardware/generic/goldfish/emulator/ui/aquarium/node_modules/ts-proto/build/src/types.js"(exports2) {
    "use strict";
    Object.defineProperty(exports2, "__esModule", { value: true });
    exports2.basicWireType = basicWireType;
    exports2.basicLongWireType = basicLongWireType;
    exports2.basicTypeName = basicTypeName;
    exports2.toReaderCall = toReaderCall;
    exports2.packedField = packedField;
    exports2.packedType = packedType;
    exports2.getFieldOptionsJsType = getFieldOptionsJsType;
    exports2.defaultValue = defaultValue;
    exports2.notDefaultCheck = notDefaultCheck;
    exports2.createTypeMap = createTypeMap;
    exports2.isScalar = isScalar;
    exports2.isOptionalProperty = isOptionalProperty;
    exports2.isPrimitive = isPrimitive;
    exports2.isBytes = isBytes;
    exports2.isMessage = isMessage;
    exports2.isEnum = isEnum;
    exports2.isWithinOneOf = isWithinOneOf;
    exports2.isWithinOneOfThatShouldBeUnion = isWithinOneOfThatShouldBeUnion;
    exports2.isRepeated = isRepeated;
    exports2.isLong = isLong;
    exports2.isWholeNumber = isWholeNumber;
    exports2.isMapType = isMapType;
    exports2.isObjectId = isObjectId;
    exports2.isTimestamp = isTimestamp;
    exports2.isValueType = isValueType;
    exports2.isAnyValueType = isAnyValueType;
    exports2.isAnyValueTypeName = isAnyValueTypeName;
    exports2.isBytesValueType = isBytesValueType;
    exports2.isFieldMaskType = isFieldMaskType;
    exports2.isFieldMaskTypeName = isFieldMaskTypeName;
    exports2.isListValueType = isListValueType;
    exports2.isListValueTypeName = isListValueTypeName;
    exports2.isStructType = isStructType;
    exports2.isStructTypeName = isStructTypeName;
    exports2.isLongValueType = isLongValueType;
    exports2.isEmptyType = isEmptyType;
    exports2.valueTypeName = valueTypeName;
    exports2.wrapperTypeName = wrapperTypeName;
    exports2.messageToTypeName = messageToTypeName;
    exports2.getEnumMethod = getEnumMethod;
    exports2.toTypeName = toTypeName;
    exports2.shouldGenerateJSMapType = shouldGenerateJSMapType;
    exports2.detectMapType = detectMapType;
    exports2.rawRequestType = rawRequestType;
    exports2.observableType = observableType;
    exports2.requestType = requestType;
    exports2.responseType = responseType;
    exports2.responsePromise = responsePromise;
    exports2.responseObservable = responseObservable;
    exports2.responsePromiseOrObservable = responsePromiseOrObservable;
    exports2.detectBatchMethod = detectBatchMethod;
    exports2.isJsTypeFieldOption = isJsTypeFieldOption;
    var ts_poet_1 = require_build();
    var ts_proto_descriptors_12 = require_dist();
    var case_1 = require_case();
    var enums_1 = require_enums();
    var options_12 = require_options();
    var sourceInfo_1 = require_sourceInfo();
    var utils_12 = require_utils2();
    var visit_1 = require_visit();
    function basicWireType(type) {
      switch (type) {
        case ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_DOUBLE:
          return 1;
        case ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_FLOAT:
          return 5;
        case ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_INT32:
        case ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_ENUM:
        case ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_UINT32:
        case ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_SINT32:
          return 0;
        case ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_FIXED32:
        case ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_SFIXED32:
          return 5;
        case ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_INT64:
        case ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_UINT64:
        case ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_SINT64:
          return 0;
        case ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_FIXED64:
        case ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_SFIXED64:
          return 1;
        case ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_BOOL:
          return 0;
        case ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_STRING:
        case ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_BYTES:
        case ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_MESSAGE:
          return 2;
        case ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_GROUP:
          return 3;
        default:
          throw new Error("Invalid type " + type);
      }
    }
    function basicLongWireType(type) {
      switch (type) {
        case ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_INT64:
        case ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_UINT64:
        case ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_SINT64:
          return 0;
        case ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_FIXED64:
        case ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_SFIXED64:
          return 1;
        default:
          return void 0;
      }
    }
    function basicTypeName(ctx, field, typeOptions = {}) {
      const { options } = ctx;
      const fieldType = getFieldOptionsJsType(field, ctx.options) ?? field.type;
      switch (fieldType) {
        case ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_DOUBLE:
        case ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_FLOAT:
        case ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_INT32:
        case ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_UINT32:
        case ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_SINT32:
        case ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_FIXED32:
        case ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_SFIXED32:
          return (0, ts_poet_1.code)`number`;
        case ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_INT64:
        case ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_UINT64:
        case ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_SINT64:
        case ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_FIXED64:
        case ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_SFIXED64:
          return isJsTypeFieldOption(options, field) ? jsTypeName(field) ?? longTypeName(ctx) : (
            // this handles 2^53, Long is only needed for 2^64; this is effectively pbjs's forceNumber
            longTypeName(ctx)
          );
        case ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_BOOL:
          return (0, ts_poet_1.code)`boolean`;
        case ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_STRING:
          return (0, ts_poet_1.code)`string`;
        case ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_BYTES:
          if (options.env === options_12.EnvOption.NODE) {
            return (0, ts_poet_1.code)`Buffer`;
          } else {
            return (0, ts_poet_1.code)`Uint8Array`;
          }
        case ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_MESSAGE:
        case ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_GROUP:
        case ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_ENUM:
          return messageToTypeName(ctx, field.typeName, { ...typeOptions, repeated: isRepeated(field) });
        default:
          return (0, ts_poet_1.code)`${field.typeName}`;
      }
    }
    function toReaderCall(field) {
      switch (field.type) {
        case ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_DOUBLE:
          return "double";
        case ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_FLOAT:
          return "float";
        case ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_INT32:
        case ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_ENUM:
          return "int32";
        case ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_UINT32:
          return "uint32";
        case ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_SINT32:
          return "sint32";
        case ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_FIXED32:
          return "fixed32";
        case ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_SFIXED32:
          return "sfixed32";
        case ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_INT64:
          return "int64";
        case ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_UINT64:
          return "uint64";
        case ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_SINT64:
          return "sint64";
        case ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_FIXED64:
          return "fixed64";
        case ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_SFIXED64:
          return "sfixed64";
        case ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_BOOL:
          return "bool";
        case ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_STRING:
          return "string";
        case ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_BYTES:
          return "bytes";
        default:
          throw new Error(`Not a primitive field ${field}`);
      }
    }
    function packedField(field, isProto3Syntax) {
      const shouldPack = field.options?.packed ?? isProto3Syntax;
      return shouldPack ? packedType(field.type) : void 0;
    }
    function packedType(type) {
      switch (type) {
        case ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_DOUBLE:
          return 1;
        case ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_FLOAT:
          return 5;
        case ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_INT32:
        case ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_ENUM:
        case ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_UINT32:
        case ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_SINT32:
          return 0;
        case ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_FIXED32:
        case ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_SFIXED32:
          return 5;
        case ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_INT64:
        case ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_UINT64:
        case ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_SINT64:
          return 0;
        case ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_FIXED64:
        case ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_SFIXED64:
          return 1;
        case ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_BOOL:
          return 0;
        default:
          return void 0;
      }
    }
    function getFieldOptionsJsType(field, options) {
      if (!options.useJsTypeOverride || field.options?.jstype === void 0) {
        return;
      }
      switch (field.options.jstype) {
        case ts_proto_descriptors_12.FieldOptions_JSType.JS_STRING:
          return ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_STRING;
        case ts_proto_descriptors_12.FieldOptions_JSType.JS_NUMBER:
          return ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_INT64;
        case ts_proto_descriptors_12.FieldOptions_JSType.JS_NORMAL:
        case ts_proto_descriptors_12.FieldOptions_JSType.UNRECOGNIZED:
          return;
      }
    }
    function defaultValue(ctx, field) {
      const { typeMap, options, utils, currentFile } = ctx;
      if (options.noDefaultsForOptionals) {
        return options.useNullAsOptional ? null : void 0;
      }
      const useDefaultValue = !currentFile.isProto3Syntax && !options.disableProto2DefaultValues && field.defaultValue;
      const numericDefaultVal = useDefaultValue ? field.defaultValue : 0;
      switch (field.type) {
        case ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_DOUBLE:
        case ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_FLOAT:
        case ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_INT32:
        case ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_UINT32:
        case ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_SINT32:
        case ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_FIXED32:
        case ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_SFIXED32:
          return numericDefaultVal;
        case ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_ENUM:
          const typeInfo = typeMap.get(field.typeName);
          const enumProto = typeInfo[2];
          const defaultEnum = enumProto.value.find((v) => useDefaultValue ? v.name === field.defaultValue : v.number === 0) || enumProto.value[0];
          if (options.stringEnums) {
            const enumType = messageToTypeName(ctx, field.typeName);
            return (0, ts_poet_1.code)`${enumType}.${(0, enums_1.getMemberName)(ctx, enumProto, defaultEnum)}`;
          } else {
            return defaultEnum.number;
          }
        case ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_INT64:
        case ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_UINT64:
        case ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_FIXED64:
        case ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_SINT64:
        case ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_SFIXED64:
          if (isJsTypeFieldOption(options, field)) {
            switch (field.options.jstype) {
              case ts_proto_descriptors_12.FieldOptions_JSType.JS_STRING:
                return `"${numericDefaultVal}"`;
              case ts_proto_descriptors_12.FieldOptions_JSType.JS_NUMBER:
                return numericDefaultVal;
            }
          }
          if (options.forceLong === options_12.LongOption.LONG) {
            const value = field.type === ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_UINT64 || field.type === ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_FIXED64 ? "UZERO" : "ZERO";
            return (0, ts_poet_1.code)`${utils.Long}.${useDefaultValue ? "fromNumber" : value}${useDefaultValue ? `(${numericDefaultVal})` : ""}`;
          } else if (options.forceLong === options_12.LongOption.STRING) {
            return `"${numericDefaultVal}"`;
          } else if (options.forceLong === options_12.LongOption.BIGINT) {
            return options.bigIntLiteral ? (0, ts_poet_1.code)`${numericDefaultVal}n` : (0, ts_poet_1.code)`BigInt("${numericDefaultVal}")`;
          } else {
            return numericDefaultVal;
          }
        case ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_BOOL:
          return useDefaultValue ? field.defaultValue : false;
        case ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_STRING:
          return useDefaultValue ? JSON.stringify(field.defaultValue) : '""';
        case ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_BYTES:
          if (options.env === options_12.EnvOption.NODE) {
            return "Buffer.alloc(0)";
          }
          return "new Uint8Array(0)";
        case ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_MESSAGE:
        case ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_GROUP:
        default:
          return (0, utils_12.nullOrUndefined)(options);
      }
    }
    function notDefaultCheck(ctx, field, messageOptions, place) {
      const { typeMap, options, currentFile } = ctx;
      const isOptional = isOptionalProperty(field, messageOptions, options, currentFile.isProto3Syntax);
      if (options.noDefaultsForOptionals) {
        return isOptional ? (0, ts_poet_1.code)`${place} !== undefined ${(0, utils_12.withAndMaybeCheckIsNotNull)(options, place)}` : (0, ts_poet_1.code)`${place} !== undefined`;
      }
      const maybeNotUndefinedAnd = isOptional ? `${place} !== undefined ${(0, utils_12.withAndMaybeCheckIsNotNull)(options, place)} &&` : "";
      switch (field.type) {
        case ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_DOUBLE:
        case ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_FLOAT:
        case ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_INT32:
        case ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_UINT32:
        case ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_SINT32:
        case ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_FIXED32:
        case ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_SFIXED32:
        case ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_BOOL:
        case ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_STRING:
          return (0, ts_poet_1.code)`${maybeNotUndefinedAnd} ${place} !== ${defaultValue(ctx, field)}`;
        case ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_ENUM:
          const typeInfo = typeMap.get(field.typeName);
          const enumProto = typeInfo[2];
          const defaultEnum = enumProto.value.find((v) => v.number === defaultValue(ctx, field)) || enumProto.value[0];
          if (options.stringEnums) {
            const enumType = messageToTypeName(ctx, field.typeName);
            const enumValue = (0, enums_1.getMemberName)(ctx, enumProto, defaultEnum);
            return (0, ts_poet_1.code)`${maybeNotUndefinedAnd} ${place} !== ${enumType}.${enumValue}`;
          } else {
            return (0, ts_poet_1.code)`${maybeNotUndefinedAnd} ${place} !== ${defaultEnum.number}`;
          }
        case ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_UINT64:
        case ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_FIXED64:
        case ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_INT64:
        case ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_SINT64:
        case ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_SFIXED64:
          if (options.forceLong === options_12.LongOption.LONG && !isJsTypeFieldOption(options, field)) {
            return (0, ts_poet_1.code)`${maybeNotUndefinedAnd} !${place}.equals(${defaultValue(ctx, field)})`;
          } else {
            return (0, ts_poet_1.code)`${maybeNotUndefinedAnd} ${place} !== ${defaultValue(ctx, field)}`;
          }
        case ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_BYTES:
          return (0, ts_poet_1.code)`${maybeNotUndefinedAnd} ${place}.length !== 0`;
        default:
          throw new Error("Not implemented for the given type.");
      }
    }
    function createTypeMap(request, options) {
      const typeMap = /* @__PURE__ */ new Map();
      for (const file of request.protoFile) {
        let saveMapping = function(tsFullName, desc, s, protoFullName) {
          const prefix = file.package.length === 0 ? "" : `.${file.package}`;
          typeMap.set(`${prefix}.${protoFullName}`, [moduleName, tsFullName, desc]);
        };
        const moduleName = file.name.replace(".proto", "");
        (0, visit_1.visit)(file, sourceInfo_1.default.empty(), saveMapping, options, saveMapping);
      }
      return typeMap;
    }
    function isScalar(field) {
      const scalarTypes = [
        ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_DOUBLE,
        ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_FLOAT,
        ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_INT32,
        ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_INT64,
        ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_UINT32,
        ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_UINT64,
        ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_SINT32,
        ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_SINT64,
        ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_FIXED32,
        ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_FIXED64,
        ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_SFIXED32,
        ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_SFIXED64,
        ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_BOOL,
        ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_STRING,
        ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_BYTES
      ];
      return scalarTypes.includes(field.type);
    }
    function isOptionalProperty(field, messageOptions, options, isProto3Syntax) {
      const optionalMessages = options.useOptionals === true || options.useOptionals === "messages" || options.useOptionals === "all";
      const optionalAll = options.useOptionals === "all";
      const deprecatedOnly = options.useOptionals === "deprecatedOnly" && field.options && field.options.deprecated;
      return optionalMessages && isMessage(field) && !isRepeated(field) || (optionalAll || deprecatedOnly) && !messageOptions?.mapEntry || options.noDefaultsForOptionals && !isRepeated(field) && (isScalar(field) || isEnum(field)) || // file is proto2, we have enabled proto2 optionals, and the field itself is optional
      !isProto3Syntax && field.label === ts_proto_descriptors_12.FieldDescriptorProto_Label.LABEL_OPTIONAL && !messageOptions?.mapEntry && !options.disableProto2Optionals || // don't bother verifying that oneof is not union. union oneofs generate their own properties.
      isWithinOneOf(field) || field.proto3Optional;
    }
    function isPrimitive(field) {
      return !isMessage(field);
    }
    function isBytes(field) {
      return field.type === ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_BYTES;
    }
    function isMessage(field) {
      return field.type === ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_MESSAGE || field.type === ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_GROUP;
    }
    function isEnum(field) {
      return field.type === ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_ENUM;
    }
    function isWithinOneOf(field) {
      return field.hasOwnProperty("oneofIndex");
    }
    function isWithinOneOfThatShouldBeUnion(options, field) {
      return isWithinOneOf(field) && (options.oneof === options_12.OneofOption.UNIONS || options.oneof === options_12.OneofOption.UNIONS_VALUE) && !field.proto3Optional;
    }
    function isRepeated(field) {
      return field.label === ts_proto_descriptors_12.FieldDescriptorProto_Label.LABEL_REPEATED;
    }
    function isLong(field) {
      return basicLongWireType(field.type) !== void 0;
    }
    function isWholeNumber(field) {
      return field.type === ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_INT32 || field.type === ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_INT64 || field.type === ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_UINT32 || field.type === ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_UINT64 || field.type === ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_SINT32 || field.type === ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_SINT64 || field.type === ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_FIXED32 || field.type === ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_FIXED64 || field.type === ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_SFIXED32 || field.type === ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_SFIXED64;
    }
    function isMapType(ctx, messageDesc, field) {
      return detectMapType(ctx, messageDesc, field) !== void 0;
    }
    function isObjectId(field) {
      return field.typeName.endsWith(".ObjectId");
    }
    function isTimestamp(field) {
      return field.typeName === ".google.protobuf.Timestamp";
    }
    function isValueType(ctx, field) {
      return valueTypeName(ctx, field.typeName) !== void 0;
    }
    function isAnyValueType(field) {
      return isAnyValueTypeName(field.typeName);
    }
    function isAnyValueTypeName(typeName) {
      return typeName === "google.protobuf.Value" || typeName === ".google.protobuf.Value";
    }
    function isBytesValueType(field) {
      return field.typeName === ".google.protobuf.BytesValue";
    }
    function isFieldMaskType(field) {
      return isFieldMaskTypeName(field.typeName);
    }
    function isFieldMaskTypeName(typeName) {
      return typeName === "google.protobuf.FieldMask" || typeName === ".google.protobuf.FieldMask";
    }
    function isListValueType(field) {
      return isListValueTypeName(field.typeName);
    }
    function isListValueTypeName(typeName) {
      return typeName === "google.protobuf.ListValue" || typeName === ".google.protobuf.ListValue";
    }
    function isStructType(field) {
      return isStructTypeName(field.typeName);
    }
    function isStructTypeName(typeName) {
      return typeName === "google.protobuf.Struct" || typeName === ".google.protobuf.Struct";
    }
    function isLongValueType(field) {
      return field.typeName === ".google.protobuf.Int64Value" || field.typeName === ".google.protobuf.UInt64Value";
    }
    function isEmptyType(typeName) {
      return typeName === ".google.protobuf.Empty";
    }
    function valueTypeName(ctx, typeName) {
      switch (typeName) {
        case ".google.protobuf.StringValue":
          return (0, ts_poet_1.code)`string`;
        case ".google.protobuf.Int32Value":
        case ".google.protobuf.UInt32Value":
        case ".google.protobuf.DoubleValue":
        case ".google.protobuf.FloatValue":
          return (0, ts_poet_1.code)`number`;
        case ".google.protobuf.Int64Value":
        case ".google.protobuf.UInt64Value":
          return longTypeName(ctx);
        case ".google.protobuf.BoolValue":
          return (0, ts_poet_1.code)`boolean`;
        case ".google.protobuf.BytesValue":
          return ctx.options.env === options_12.EnvOption.NODE ? (0, ts_poet_1.code)`Buffer` : ctx.options.useJsonWireFormat ? (0, ts_poet_1.code)`string` : (0, ts_poet_1.code)`Uint8Array`;
        case ".google.protobuf.ListValue":
          return ctx.options.useReadonlyTypes ? (0, ts_poet_1.code)`ReadonlyArray<any>` : (0, ts_poet_1.code)`Array<any>`;
        case ".google.protobuf.Value":
          return (0, ts_poet_1.code)`any`;
        case ".google.protobuf.Struct":
          return ctx.options.useReadonlyTypes ? (0, ts_poet_1.code)`{readonly [key: string]: any}` : (0, ts_poet_1.code)`{[key: string]: any}`;
        case ".google.protobuf.FieldMask":
          return ctx.options.useJsonWireFormat ? (0, ts_poet_1.code)`string` : ctx.options.useReadonlyTypes ? (0, ts_poet_1.code)`readonly string[]` : (0, ts_poet_1.code)`string[]`;
        case ".google.protobuf.Duration":
          return ctx.options.useJsonWireFormat ? (0, ts_poet_1.code)`string` : void 0;
        case ".google.protobuf.Timestamp":
          return ctx.options.useJsonWireFormat ? (0, ts_poet_1.code)`string` : void 0;
        default:
          return void 0;
      }
    }
    function wrapperTypeName(typeName) {
      switch (typeName) {
        case ".google.protobuf.StringValue":
        case ".google.protobuf.Int32Value":
        case ".google.protobuf.UInt32Value":
        case ".google.protobuf.DoubleValue":
        case ".google.protobuf.FloatValue":
        case ".google.protobuf.Int64Value":
        case ".google.protobuf.UInt64Value":
        case ".google.protobuf.BoolValue":
        case ".google.protobuf.BytesValue":
        case ".google.protobuf.ListValue":
        case ".google.protobuf.Timestamp":
        case ".google.protobuf.Struct":
        case ".google.protobuf.Value":
          return typeName.split(".")[3];
        default:
          return void 0;
      }
    }
    function longTypeName(ctx) {
      const { options, utils } = ctx;
      if (options.forceLong === options_12.LongOption.LONG) {
        return (0, ts_poet_1.code)`${utils.Long}`;
      } else if (options.forceLong === options_12.LongOption.STRING) {
        return (0, ts_poet_1.code)`string`;
      } else if (options.forceLong === options_12.LongOption.BIGINT) {
        return (0, ts_poet_1.code)`bigint`;
      } else {
        return (0, ts_poet_1.code)`number`;
      }
    }
    function jsTypeName(field) {
      if (field.options?.jstype === ts_proto_descriptors_12.FieldOptions_JSType.JS_STRING) {
        return (0, ts_poet_1.code)`string`;
      } else if (field.options?.jstype === ts_proto_descriptors_12.FieldOptions_JSType.JS_NUMBER) {
        return (0, ts_poet_1.code)`number`;
      }
    }
    function messageToTypeName(ctx, protoType, typeOptions = {}) {
      const { options, typeMap } = ctx;
      let valueType = valueTypeName(ctx, protoType);
      if (!typeOptions.keepValueType && valueType) {
        if (typeOptions.repeated ?? false) {
          return valueType;
        }
        return (0, ts_poet_1.code)`${valueType} | ${(0, utils_12.nullOrUndefined)(options)}`;
      }
      if (!typeOptions.keepValueType && protoType === ".google.protobuf.Timestamp") {
        if (options.useDate == options_12.DateOption.DATE) {
          return (0, ts_poet_1.code)`Date`;
        }
        if (options.useDate === options_12.DateOption.TEMPORAL) {
          return (0, ts_poet_1.code)`Temporal.Instant`;
        }
        if (options.useDate == options_12.DateOption.STRING || options.useDate == options_12.DateOption.STRING_NANO) {
          return (0, ts_poet_1.code)`string`;
        }
      }
      if (!typeOptions.keepValueType && options.useMongoObjectId && protoType.endsWith(".ObjectId")) {
        return (0, ts_poet_1.code)`mongodb.ObjectId`;
      }
      const [module3, type] = toModuleAndType(typeMap, protoType);
      return (0, ts_poet_1.code)`${(0, utils_12.impProto)(options, module3, type)}`;
    }
    function toModuleAndType(typeMap, protoType) {
      return typeMap.get(protoType) || (0, utils_12.fail)(`No type found for ${protoType}`);
    }
    function getEnumMethod(ctx, enumProtoType, methodSuffix) {
      const [module3, type] = toModuleAndType(ctx.typeMap, enumProtoType);
      return (0, utils_12.impProto)(ctx.options, module3, `${(0, case_1.uncapitalize)(type)}${methodSuffix}`);
    }
    function toTypeName(ctx, messageDesc, field, ensureOptional = false) {
      function finalize(type2, isOptional) {
        if (isOptional) {
          return (0, ts_poet_1.code)`${type2} | ${(0, utils_12.nullOrUndefined)(ctx.options, field.proto3Optional)}`;
        }
        return type2;
      }
      const fieldType = getFieldOptionsJsType(field, ctx.options) ?? field.type;
      const type = basicTypeName(ctx, { ...field, type: fieldType }, { keepValueType: false });
      if (isRepeated(field)) {
        const mapType = messageDesc ? detectMapType(ctx, messageDesc, field) : false;
        if (mapType) {
          const { keyType, valueType } = mapType;
          if (shouldGenerateJSMapType(ctx, messageDesc, field)) {
            return finalize((0, ts_poet_1.code)`Map<${keyType}, ${valueType}>`, ensureOptional);
          }
          return finalize((0, ts_poet_1.code)`{ [key: ${keyType} ]: ${valueType} }`, ensureOptional);
        }
        if (ctx.options.useReadonlyTypes) {
          return finalize((0, ts_poet_1.code)`readonly ${type}[]`, ensureOptional);
        }
        return finalize((0, ts_poet_1.code)`${type}[]`, ensureOptional);
      }
      if (isValueType(ctx, field)) {
        return finalize(type, false);
      }
      const { options } = ctx;
      return finalize(type, !isWithinOneOf(field) && isMessage(field) && (options.useOptionals === false || options.useOptionals === "none") || isWithinOneOf(field) && options.oneof === options_12.OneofOption.PROPERTIES || isWithinOneOf(field) && field.proto3Optional || ensureOptional);
    }
    function shouldGenerateJSMapType(ctx, message, field) {
      if (ctx.options.useMapType) {
        return true;
      }
      const mapType = detectMapType(ctx, message, field);
      if (!mapType) {
        return false;
      }
      return mapType.keyField.type === ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_BOOL || isLong(mapType.keyField) && (ctx.options.forceLong === options_12.LongOption.LONG || ctx.options.forceLong === options_12.LongOption.BIGINT);
    }
    function detectMapType(ctx, messageDesc, fieldDesc) {
      const { typeMap } = ctx;
      if (fieldDesc.label === ts_proto_descriptors_12.FieldDescriptorProto_Label.LABEL_REPEATED && fieldDesc.type === ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_MESSAGE) {
        const mapType = typeMap.get(fieldDesc.typeName)[2];
        if (!mapType.options?.mapEntry)
          return void 0;
        const [keyField, valueField] = mapType.field;
        const keyType = toTypeName(ctx, messageDesc, keyField);
        const valueType = basicTypeName(ctx, valueField);
        return { messageDesc: mapType, keyField, keyType, valueField, valueType };
      }
      return void 0;
    }
    function rawRequestType(ctx, methodDesc, typeOptions = {}) {
      return messageToTypeName(ctx, methodDesc.inputType, typeOptions);
    }
    function observableType(ctx, asType = false) {
      if (ctx.options.useAsyncIterable) {
        return (0, ts_poet_1.code)`AsyncIterable`;
      } else if (asType) {
        return (0, ts_poet_1.code)`${(0, ts_poet_1.imp)("t:Observable@rxjs")}`;
      } else {
        return (0, ts_poet_1.code)`${(0, ts_poet_1.imp)("Observable@rxjs")}`;
      }
    }
    function requestType(ctx, methodDesc, partial = false) {
      let typeName = rawRequestType(ctx, methodDesc, { keepValueType: true });
      if (partial) {
        typeName = (0, ts_poet_1.code)`${ctx.utils.DeepPartial}<${typeName}>`;
      }
      if (methodDesc.clientStreaming) {
        return (0, ts_poet_1.code)`${observableType(ctx)}<${typeName}>`;
      }
      return typeName;
    }
    function responseType(ctx, methodDesc, typeOptions = {}) {
      return messageToTypeName(ctx, methodDesc.outputType, { keepValueType: true });
    }
    function responsePromise(ctx, methodDesc) {
      return (0, ts_poet_1.code)`Promise<${responseType(ctx, methodDesc, { keepValueType: true })}>`;
    }
    function responseObservable(ctx, methodDesc) {
      return (0, ts_poet_1.code)`${observableType(ctx)}<${responseType(ctx, methodDesc, { keepValueType: true })}>`;
    }
    function responsePromiseOrObservable(ctx, methodDesc) {
      const { options } = ctx;
      if (options.returnObservable || methodDesc.serverStreaming) {
        return responseObservable(ctx, methodDesc);
      }
      return responsePromise(ctx, methodDesc);
    }
    function detectBatchMethod(ctx, fileDesc, serviceDesc, methodDesc) {
      const { typeMap } = ctx;
      const nameMatches = methodDesc.name.startsWith("Batch");
      const inputType = typeMap.get(methodDesc.inputType);
      const outputType = typeMap.get(methodDesc.outputType);
      if (nameMatches && inputType && outputType) {
        const inputTypeDesc = inputType[2];
        const outputTypeDesc = outputType[2];
        if (hasSingleRepeatedField(inputTypeDesc) && hasSingleRepeatedField(outputTypeDesc)) {
          const singleMethodName = methodDesc.name.replace("Batch", "Get");
          const inputFieldName = inputTypeDesc.field[0].name;
          const inputType2 = basicTypeName(ctx, inputTypeDesc.field[0]);
          const outputFieldName = outputTypeDesc.field[0].name;
          let outputType2 = basicTypeName(ctx, outputTypeDesc.field[0]);
          const mapType = detectMapType(ctx, outputTypeDesc, outputTypeDesc.field[0]);
          if (mapType) {
            outputType2 = mapType.valueType;
          }
          const uniqueIdentifier = `${(0, utils_12.maybePrefixPackage)(fileDesc, serviceDesc.name)}.${methodDesc.name}`;
          return {
            methodDesc,
            uniqueIdentifier,
            singleMethodName: utils_12.FormattedMethodDescriptor.formatName(singleMethodName, ctx.options),
            inputFieldName,
            inputType: inputType2,
            outputFieldName,
            outputType: outputType2,
            mapType: !!mapType
          };
        }
      }
      return void 0;
    }
    function hasSingleRepeatedField(messageDesc) {
      return messageDesc.field.length == 1 && messageDesc.field[0].label === ts_proto_descriptors_12.FieldDescriptorProto_Label.LABEL_REPEATED;
    }
    function isJsTypeFieldOption(options, field) {
      return options.useJsTypeOverride && (field.options?.jstype === ts_proto_descriptors_12.FieldOptions_JSType.JS_NUMBER || field.options?.jstype === ts_proto_descriptors_12.FieldOptions_JSType.JS_STRING);
    }
  }
});

// hardware/generic/goldfish/emulator/ui/aquarium/node_modules/ts-proto/build/src/generate-generic-service-definition.js
var require_generate_generic_service_definition = __commonJS({
  "hardware/generic/goldfish/emulator/ui/aquarium/node_modules/ts-proto/build/src/generate-generic-service-definition.js"(exports2) {
    "use strict";
    Object.defineProperty(exports2, "__esModule", { value: true });
    exports2.generateGenericServiceDefinition = generateGenericServiceDefinition;
    var ts_poet_1 = require_build();
    var ts_proto_descriptors_12 = require_dist();
    var case_1 = require_case();
    var sourceInfo_1 = require_sourceInfo();
    var types_12 = require_types();
    var utils_12 = require_utils2();
    function generateGenericServiceDefinition(ctx, fileDesc, sourceInfo, serviceDesc) {
      const chunks = [];
      (0, utils_12.maybeAddComment)(ctx.options, sourceInfo, chunks, serviceDesc.options?.deprecated);
      const name = (0, ts_poet_1.def)(`${serviceDesc.name}Definition`);
      chunks.push((0, ts_poet_1.code)`
    export type ${name} = typeof ${name};
  `);
      chunks.push((0, ts_poet_1.code)`
    export const ${name} = {
  `);
      serviceDesc.options?.uninterpretedOption;
      chunks.push((0, ts_poet_1.code)`
      name: '${serviceDesc.name}',
      fullName: '${(0, utils_12.maybePrefixPackage)(fileDesc, serviceDesc.name)}',
      methods: {
  `);
      for (const [index, methodDesc] of serviceDesc.method.entries()) {
        const info = sourceInfo.lookup(sourceInfo_1.Fields.service.method, index);
        (0, utils_12.maybeAddComment)(ctx.options, info, chunks, methodDesc.options?.deprecated);
        chunks.push((0, ts_poet_1.code)`
      ${(0, case_1.uncapitalize)(methodDesc.name)}: ${generateMethodDefinition(ctx, methodDesc)},
    `);
      }
      chunks.push((0, ts_poet_1.code)`
      },
    } as const;
  `);
      return (0, ts_poet_1.joinCode)(chunks, { on: "\n" });
    }
    function generateMethodDefinition(ctx, methodDesc) {
      const inputType = (0, types_12.messageToTypeName)(ctx, methodDesc.inputType, { keepValueType: true });
      const outputType = (0, types_12.messageToTypeName)(ctx, methodDesc.outputType, { keepValueType: true });
      return (0, ts_poet_1.code)`
    {
      name: '${methodDesc.name}',
      requestType: ${inputType} as typeof ${inputType},
      requestStream: ${methodDesc.clientStreaming},
      responseType: ${outputType} as typeof ${outputType},
      responseStream: ${methodDesc.serverStreaming},
      options: ${generateMethodOptions(ctx, methodDesc.options)}
    }
  `;
    }
    function generateMethodOptions(ctx, options) {
      const chunks = [];
      chunks.push((0, ts_poet_1.code)`{`);
      if (options != null) {
        if (options.idempotencyLevel === ts_proto_descriptors_12.MethodOptions_IdempotencyLevel.IDEMPOTENT) {
          chunks.push((0, ts_poet_1.code)`idempotencyLevel: 'IDEMPOTENT',`);
        } else if (options.idempotencyLevel === ts_proto_descriptors_12.MethodOptions_IdempotencyLevel.NO_SIDE_EFFECTS) {
          chunks.push((0, ts_poet_1.code)`idempotencyLevel: 'NO_SIDE_EFFECTS',`);
        }
        if (options._unknownFields !== void 0) {
          const unknownFieldsChunks = [];
          unknownFieldsChunks.push((0, ts_poet_1.code)`{`);
          for (const key in options._unknownFields) {
            const values = options._unknownFields[key];
            const valuesChunks = [];
            for (const value of values) {
              valuesChunks.push((0, ts_poet_1.code)`${ctx.options.env == "node" ? "Buffer.from" : "new Uint8Array"}([${value.join(", ")}]) as ${ctx.options.env == "node" ? "Buffer" : "Uint8Array"}`);
            }
            unknownFieldsChunks.push((0, ts_poet_1.code)`${key}: [\n${(0, ts_poet_1.joinCode)(valuesChunks, { on: "," })}\n],`);
          }
          unknownFieldsChunks.push((0, ts_poet_1.code)`}`);
          chunks.push((0, ts_poet_1.code)`_unknownFields: ${(0, ts_poet_1.joinCode)(unknownFieldsChunks, { on: "\n" })}`);
        }
      }
      chunks.push((0, ts_poet_1.code)`}`);
      return (0, ts_poet_1.joinCode)(chunks, { on: "\n" });
    }
  }
});

// hardware/generic/goldfish/emulator/ui/aquarium/node_modules/ts-proto/build/src/encode.js
var require_encode = __commonJS({
  "hardware/generic/goldfish/emulator/ui/aquarium/node_modules/ts-proto/build/src/encode.js"(exports2) {
    "use strict";
    Object.defineProperty(exports2, "__esModule", { value: true });
    exports2.generateEncoder = generateEncoder;
    exports2.generateDecoder = generateDecoder;
    var ts_poet_1 = require_build();
    var types_12 = require_types();
    var options_12 = require_options();
    var utils_12 = require_utils2();
    function generateEncoder(ctx, typeName) {
      const name = (0, types_12.wrapperTypeName)(typeName);
      if (!name) {
        return (0, ts_poet_1.code)`${(0, types_12.messageToTypeName)(ctx, typeName, { keepValueType: true })}.encode(value).finish()`;
      }
      if (name == "Timestamp") {
        const TimestampValue = (0, utils_12.impProto)(ctx.options, "google/protobuf/timestamp", (0, utils_12.wrapTypeName)(ctx.options, name));
        let value = (0, ts_poet_1.code)`value`;
        if (ctx.options.useDate === options_12.DateOption.DATE || ctx.options.useDate === options_12.DateOption.STRING || ctx.options.useDate === options_12.DateOption.STRING_NANO || ctx.options.useDate === options_12.DateOption.TEMPORAL) {
          value = (0, ts_poet_1.code)`${ctx.utils.toTimestamp}(${value})`;
        }
        return (0, ts_poet_1.code)`${TimestampValue}.encode(${value}).finish()`;
      }
      if (name == "Struct" || name == "Value") {
        const StructType = (0, utils_12.impProto)(ctx.options, "google/protobuf/struct", (0, utils_12.wrapTypeName)(ctx.options, name));
        return (0, ts_poet_1.code)`${StructType}.encode(${StructType}.wrap(value)).finish()`;
      }
      if (name == "ListValue") {
        const ListValueType = (0, utils_12.impProto)(ctx.options, "google/protobuf/struct", (0, utils_12.wrapTypeName)(ctx.options, name));
        return (0, ts_poet_1.code)`${ListValueType}.encode({values: value ?? []}).finish()`;
      }
      const TypeValue = (0, utils_12.impProto)(ctx.options, "google/protobuf/wrappers", (0, utils_12.wrapTypeName)(ctx.options, name));
      switch (name) {
        case "StringValue":
          return (0, ts_poet_1.code)`${TypeValue}.encode({value: value ?? ""}).finish()`;
        case "Int32Value":
        case "UInt32Value":
        case "DoubleValue":
        case "FloatValue":
          return (0, ts_poet_1.code)`${TypeValue}.encode({value: value ?? 0}).finish()`;
        case "Int64Value":
        case "UInt64Value":
          if (ctx.options.forceLong === options_12.LongOption.LONG) {
            return (0, ts_poet_1.code)`${TypeValue}.encode({value: value ? value.toNumber(): 0}).finish()`;
          }
          return (0, ts_poet_1.code)`${TypeValue}.encode({value: value ?? 0 }).finish()`;
        case "BoolValue":
          return (0, ts_poet_1.code)`${TypeValue}.encode({value: value ?? false}).finish()`;
        case "BytesValue":
          return (0, ts_poet_1.code)`${TypeValue}.encode({value: value ?? new Uint8Array(0)}).finish()`;
      }
      throw new Error(`unknown wrapper type: ${name}`);
    }
    function generateDecoder(ctx, typeName) {
      const { options } = ctx;
      let name = (0, types_12.wrapperTypeName)(typeName);
      if (!name) {
        return (0, ts_poet_1.code)`${(0, types_12.messageToTypeName)(ctx, typeName, { keepValueType: true })}.decode(value)`;
      }
      let TypeValue;
      if (name == "Timestamp") {
        TypeValue = (0, utils_12.impProto)(ctx.options, "google/protobuf/timestamp", (0, utils_12.wrapTypeName)(ctx.options, name));
        const decoder = (0, ts_poet_1.code)`${TypeValue}.decode(value)`;
        if (ctx.options.useDate === options_12.DateOption.DATE || ctx.options.useDate === options_12.DateOption.STRING || ctx.options.useDate === options_12.DateOption.STRING_NANO || ctx.options.useDate === options_12.DateOption.TEMPORAL) {
          return (0, ts_poet_1.code)`${ctx.utils.fromTimestamp}(${decoder})`;
        }
        return decoder;
      }
      if (name == "Struct" || name == "ListValue" || name == "Value") {
        TypeValue = (0, utils_12.impProto)(ctx.options, "google/protobuf/struct", (0, utils_12.wrapTypeName)(ctx.options, name));
        return (0, ts_poet_1.code)`${TypeValue}.unwrap(${TypeValue}.decode(value))`;
      }
      TypeValue = (0, utils_12.impProto)(ctx.options, "google/protobuf/wrappers", (0, utils_12.wrapTypeName)(ctx.options, name));
      return (0, ts_poet_1.code)`${TypeValue}.decode(value).value`;
    }
  }
});

// hardware/generic/goldfish/emulator/ui/aquarium/node_modules/ts-proto/build/src/generate-grpc-js.js
var require_generate_grpc_js = __commonJS({
  "hardware/generic/goldfish/emulator/ui/aquarium/node_modules/ts-proto/build/src/generate-grpc-js.js"(exports2) {
    "use strict";
    Object.defineProperty(exports2, "__esModule", { value: true });
    exports2.generateGrpcJsService = generateGrpcJsService;
    var ts_poet_1 = require_build();
    var sourceInfo_1 = require_sourceInfo();
    var types_12 = require_types();
    var utils_12 = require_utils2();
    var encode_1 = require_encode();
    var CallOptions = (0, ts_poet_1.imp)("t:CallOptions@@grpc/grpc-js");
    var ChannelCredentials = (0, ts_poet_1.imp)("t:ChannelCredentials@@grpc/grpc-js");
    var ClientOptions = (0, ts_poet_1.imp)("t:ClientOptions@@grpc/grpc-js");
    var Client = (0, ts_poet_1.imp)("Client@@grpc/grpc-js");
    var ClientDuplexStream = (0, ts_poet_1.imp)("t:ClientDuplexStream@@grpc/grpc-js");
    var ClientReadableStream = (0, ts_poet_1.imp)("t:ClientReadableStream@@grpc/grpc-js");
    var ClientUnaryCall = (0, ts_poet_1.imp)("t:ClientUnaryCall@@grpc/grpc-js");
    var ClientWritableStream = (0, ts_poet_1.imp)("t:ClientWritableStream@@grpc/grpc-js");
    var handleBidiStreamingCall = (0, ts_poet_1.imp)("t:handleBidiStreamingCall@@grpc/grpc-js");
    var handleClientStreamingCall = (0, ts_poet_1.imp)("t:handleClientStreamingCall@@grpc/grpc-js");
    var handleServerStreamingCall = (0, ts_poet_1.imp)("t:handleServerStreamingCall@@grpc/grpc-js");
    var handleUnaryCall = (0, ts_poet_1.imp)("t:handleUnaryCall@@grpc/grpc-js");
    var UntypedServiceImplementation = (0, ts_poet_1.imp)("t:UntypedServiceImplementation@@grpc/grpc-js");
    var makeGenericClientConstructor = (0, ts_poet_1.imp)("makeGenericClientConstructor@@grpc/grpc-js");
    var Metadata = (0, ts_poet_1.imp)("t:Metadata@@grpc/grpc-js");
    var ServiceError = (0, ts_poet_1.imp)("t:ServiceError@@grpc/grpc-js");
    function generateGrpcJsService(ctx, fileDesc, sourceInfo, serviceDesc) {
      const { options } = ctx;
      const chunks = [];
      chunks.push(generateServiceDefinition(ctx, fileDesc, sourceInfo, serviceDesc));
      chunks.push(generateServerStub(ctx, sourceInfo, serviceDesc));
      if (options.outputClientImpl) {
        chunks.push(generateClientStub(ctx, sourceInfo, serviceDesc));
        chunks.push(generateClientConstructor(fileDesc, serviceDesc));
      }
      return (0, ts_poet_1.joinCode)(chunks, { on: "\n\n" });
    }
    function generateServiceDefinition(ctx, fileDesc, sourceInfo, serviceDesc) {
      const chunks = [];
      (0, utils_12.maybeAddComment)(ctx.options, sourceInfo, chunks, serviceDesc.options?.deprecated);
      const name = (0, ts_poet_1.def)(`${serviceDesc.name}Service`);
      chunks.push((0, ts_poet_1.code)`
    export type ${name} = typeof ${name};
  `);
      chunks.push((0, ts_poet_1.code)`
    export const ${name} = {
  `);
      for (const [index, methodDesc] of serviceDesc.method.entries()) {
        (0, utils_12.assertInstanceOf)(methodDesc, utils_12.FormattedMethodDescriptor);
        const inputType = (0, types_12.messageToTypeName)(ctx, methodDesc.inputType);
        const outputType = (0, types_12.messageToTypeName)(ctx, methodDesc.outputType);
        const info = sourceInfo.lookup(sourceInfo_1.Fields.service.method, index);
        (0, utils_12.maybeAddComment)(ctx.options, info, chunks, methodDesc.options?.deprecated);
        const inputEncoder = (0, encode_1.generateEncoder)(ctx, methodDesc.inputType);
        const outputEncoder = (0, encode_1.generateEncoder)(ctx, methodDesc.outputType);
        const inputDecoder = (0, encode_1.generateDecoder)(ctx, methodDesc.inputType);
        const outputDecoder = (0, encode_1.generateDecoder)(ctx, methodDesc.outputType);
        chunks.push((0, ts_poet_1.code)`
      ${methodDesc.formattedName}: {
        path: '/${(0, utils_12.maybePrefixPackage)(fileDesc, serviceDesc.name)}/${methodDesc.name}' as const,
        requestStream: ${methodDesc.clientStreaming} as const,
        responseStream: ${methodDesc.serverStreaming} as const,
        requestSerialize: (value: ${inputType}): Buffer =>
          Buffer.from(${inputEncoder}),
        requestDeserialize: (value: Buffer): ${inputType} => ${inputDecoder},
        responseSerialize: (value: ${outputType}): Buffer =>
          Buffer.from(${outputEncoder}),
        responseDeserialize: (value: Buffer): ${outputType} => ${outputDecoder},
      },
    `);
      }
      chunks.push((0, ts_poet_1.code)`} as const;`);
      return (0, ts_poet_1.joinCode)(chunks, { on: "\n" });
    }
    function generateServerStub(ctx, sourceInfo, serviceDesc) {
      const chunks = [];
      chunks.push((0, ts_poet_1.code)`export interface ${(0, ts_poet_1.def)(`${serviceDesc.name}Server`)} extends ${UntypedServiceImplementation} {`);
      for (const [index, methodDesc] of serviceDesc.method.entries()) {
        (0, utils_12.assertInstanceOf)(methodDesc, utils_12.FormattedMethodDescriptor);
        const inputType = (0, types_12.messageToTypeName)(ctx, methodDesc.inputType);
        const outputType = (0, types_12.messageToTypeName)(ctx, methodDesc.outputType);
        const info = sourceInfo.lookup(sourceInfo_1.Fields.service.method, index);
        (0, utils_12.maybeAddComment)(ctx.options, info, chunks, methodDesc.options?.deprecated);
        const callType = methodDesc.clientStreaming ? methodDesc.serverStreaming ? handleBidiStreamingCall : handleClientStreamingCall : methodDesc.serverStreaming ? handleServerStreamingCall : handleUnaryCall;
        chunks.push((0, ts_poet_1.code)`
      ${methodDesc.formattedName}: ${callType}<${inputType}, ${outputType}>;
    `);
      }
      chunks.push((0, ts_poet_1.code)`}`);
      return (0, ts_poet_1.joinCode)(chunks, { on: "\n" });
    }
    function generateClientStub(ctx, sourceInfo, serviceDesc) {
      const chunks = [];
      chunks.push((0, ts_poet_1.code)`export interface ${(0, ts_poet_1.def)(`${serviceDesc.name}Client`)} extends ${Client} {`);
      for (const [index, methodDesc] of serviceDesc.method.entries()) {
        (0, utils_12.assertInstanceOf)(methodDesc, utils_12.FormattedMethodDescriptor);
        const inputType = (0, types_12.messageToTypeName)(ctx, methodDesc.inputType);
        const outputType = (0, types_12.messageToTypeName)(ctx, methodDesc.outputType);
        const info = sourceInfo.lookup(sourceInfo_1.Fields.service.method, index);
        (0, utils_12.maybeAddComment)(ctx.options, info, chunks, methodDesc.options?.deprecated);
        const responseCallback = (0, ts_poet_1.code)`(error: ${ServiceError} | null, response: ${outputType}) => void`;
        if (methodDesc.clientStreaming) {
          if (methodDesc.serverStreaming) {
            chunks.push((0, ts_poet_1.code)`
          ${methodDesc.formattedName}(): ${ClientDuplexStream}<${inputType}, ${outputType}>;
          ${methodDesc.formattedName}(
            options: Partial<${CallOptions}>,
          ): ${ClientDuplexStream}<${inputType}, ${outputType}>;
          ${methodDesc.formattedName}(
            metadata: ${Metadata},
            options?: Partial<${CallOptions}>,
          ): ${ClientDuplexStream}<${inputType}, ${outputType}>;
        `);
          } else {
            chunks.push((0, ts_poet_1.code)`
          ${methodDesc.formattedName}(
            callback: ${responseCallback},
          ): ${ClientWritableStream}<${inputType}>;
          ${methodDesc.formattedName}(
            metadata: ${Metadata},
            callback: ${responseCallback},
          ): ${ClientWritableStream}<${inputType}>;
          ${methodDesc.formattedName}(
            options: Partial<${CallOptions}>,
            callback: ${responseCallback},
          ): ${ClientWritableStream}<${inputType}>;
          ${methodDesc.formattedName}(
            metadata: ${Metadata},
            options: Partial<${CallOptions}>,
            callback: ${responseCallback},
          ): ${ClientWritableStream}<${inputType}>;
        `);
          }
        } else {
          if (methodDesc.serverStreaming) {
            chunks.push((0, ts_poet_1.code)`
          ${methodDesc.formattedName}(
            request: ${inputType},
            options?: Partial<${CallOptions}>,
          ): ${ClientReadableStream}<${outputType}>;
          ${methodDesc.formattedName}(
            request: ${inputType},
            metadata?: ${Metadata},
            options?: Partial<${CallOptions}>,
          ): ${ClientReadableStream}<${outputType}>;
        `);
          } else {
            chunks.push((0, ts_poet_1.code)`
          ${methodDesc.formattedName}(
            request: ${inputType},
            callback: ${responseCallback},
          ): ${ClientUnaryCall};
          ${methodDesc.formattedName}(
            request: ${inputType},
            metadata: ${Metadata},
            callback: ${responseCallback},
          ): ${ClientUnaryCall};
          ${methodDesc.formattedName}(
            request: ${inputType},
            metadata: ${Metadata},
            options: Partial<${CallOptions}>,
            callback: ${responseCallback},
          ): ${ClientUnaryCall};
        `);
          }
        }
      }
      chunks.push((0, ts_poet_1.code)`}`);
      return (0, ts_poet_1.joinCode)(chunks, { on: "\n" });
    }
    function generateClientConstructor(fileDesc, serviceDesc) {
      return (0, ts_poet_1.code)`
    export const ${(0, ts_poet_1.def)(`${serviceDesc.name}Client`)} = ${makeGenericClientConstructor}(
      ${serviceDesc.name}Service,
      '${(0, utils_12.maybePrefixPackage)(fileDesc, serviceDesc.name)}'
    ) as unknown as {
      new (
        address: string,
        credentials: ${ChannelCredentials},
        options?: Partial<${ClientOptions}>,
      ): ${serviceDesc.name}Client;
      service: typeof ${serviceDesc.name}Service;
      serviceName: string;
    }
  `;
    }
  }
});

// hardware/generic/goldfish/emulator/ui/aquarium/node_modules/ts-proto/build/src/generate-grpc-web.js
var require_generate_grpc_web = __commonJS({
  "hardware/generic/goldfish/emulator/ui/aquarium/node_modules/ts-proto/build/src/generate-grpc-web.js"(exports2) {
    "use strict";
    Object.defineProperty(exports2, "__esModule", { value: true });
    exports2.generateGrpcClientImpl = generateGrpcClientImpl;
    exports2.generateGrpcServiceDesc = generateGrpcServiceDesc;
    exports2.generateGrpcMethodDesc = generateGrpcMethodDesc;
    exports2.addGrpcWebMisc = addGrpcWebMisc;
    var types_12 = require_types();
    var ts_poet_1 = require_build();
    var utils_12 = require_utils2();
    var grpc = (0, ts_poet_1.imp)("grpc@@improbable-eng/grpc-web");
    var share = (0, ts_poet_1.imp)("share@rxjs/operators");
    var take = (0, ts_poet_1.imp)("take@rxjs/operators");
    var BrowserHeaders = (0, ts_poet_1.imp)("BrowserHeaders@browser-headers");
    function generateGrpcClientImpl(ctx, _fileDesc, serviceDesc) {
      const chunks = [];
      chunks.push((0, ts_poet_1.code)`
    export class ${serviceDesc.name}ClientImpl implements ${serviceDesc.name} {
  `);
      chunks.push((0, ts_poet_1.code)`
    private readonly rpc: Rpc;

    constructor(rpc: Rpc) {
  `);
      chunks.push((0, ts_poet_1.code)`this.rpc = rpc;`);
      for (const methodDesc of serviceDesc.method) {
        (0, utils_12.assertInstanceOf)(methodDesc, utils_12.FormattedMethodDescriptor);
        chunks.push((0, ts_poet_1.code)`this.${methodDesc.formattedName} = this.${methodDesc.formattedName}.bind(this);`);
      }
      chunks.push((0, ts_poet_1.code)`}`);
      for (const methodDesc of serviceDesc.method) {
        chunks.push(generateRpcMethod(ctx, serviceDesc, methodDesc));
      }
      chunks.push((0, ts_poet_1.code)`}`);
      return (0, ts_poet_1.joinCode)(chunks, { trim: false, on: "\n" });
    }
    function generateRpcMethod(ctx, serviceDesc, methodDesc) {
      (0, utils_12.assertInstanceOf)(methodDesc, utils_12.FormattedMethodDescriptor);
      const { options } = ctx;
      const { useAbortSignal } = options;
      const requestMessage = (0, types_12.requestType)(ctx, methodDesc, false);
      const inputType = (0, types_12.requestType)(ctx, methodDesc, true);
      const returns = (0, types_12.responsePromiseOrObservable)(ctx, methodDesc);
      if (methodDesc.clientStreaming) {
        return (0, ts_poet_1.code)`
    ${methodDesc.formattedName}(
      request: ${inputType},
      metadata?: grpc.Metadata,
      ${useAbortSignal ? "abortSignal?: AbortSignal," : ""}
    ): ${returns} {
      throw new Error('ts-proto does not yet support client streaming!');
    }
  `;
      }
      const method = methodDesc.serverStreaming ? "invoke" : "unary";
      return (0, ts_poet_1.code)`
    ${methodDesc.formattedName}(
      request: ${inputType},
      metadata?: grpc.Metadata,
      ${useAbortSignal ? "abortSignal?: AbortSignal," : ""}
    ): ${returns} {
      return this.rpc.${method}(
        ${methodDescName(serviceDesc, methodDesc)},
        ${requestMessage}.fromPartial(request),
        metadata,
        ${useAbortSignal ? "abortSignal," : ""}
      );
    }
  `;
    }
    function generateGrpcServiceDesc(fileDesc, serviceDesc) {
      return (0, ts_poet_1.code)`
    export const ${serviceDesc.name}Desc = {
      serviceName: "${(0, utils_12.maybePrefixPackage)(fileDesc, serviceDesc.name)}",
    };
  `;
    }
    function generateGrpcMethodDesc(ctx, serviceDesc, methodDesc) {
      const inputType = (0, types_12.requestType)(ctx, methodDesc);
      const outputType = (0, types_12.responseType)(ctx, methodDesc);
      const requestFn = (0, ts_poet_1.code)`{
    serializeBinary() {
      return ${inputType}.encode(this).finish();
    },
  }`;
      const responseFn = (0, ts_poet_1.code)`{
    deserializeBinary(data: Uint8Array) {
      const value = ${outputType}.decode(data);
      return {
        ...value,
        toObject() { return value; },
      };
    }
  }`;
      return (0, ts_poet_1.code)`
    export const ${methodDescName(serviceDesc, methodDesc)}: UnaryMethodDefinitionish = {
      methodName: "${methodDesc.name}",
      service: ${serviceDesc.name}Desc,
      requestStream: false,
      responseStream: ${methodDesc.serverStreaming ? "true" : "false"},
      requestType: ${requestFn} as any,
      responseType: ${responseFn} as any,
    };
  `;
    }
    function methodDescName(serviceDesc, methodDesc) {
      return `${serviceDesc.name}${methodDesc.name}Desc`;
    }
    function addGrpcWebMisc(ctx, hasStreamingMethods) {
      const { options } = ctx;
      const chunks = [];
      chunks.push((0, ts_poet_1.code)`
    interface UnaryMethodDefinitionishR extends ${grpc}.UnaryMethodDefinition<any, any> { requestStream: any; responseStream: any; }
  `);
      chunks.push((0, ts_poet_1.code)`type UnaryMethodDefinitionish = UnaryMethodDefinitionishR;`);
      chunks.push(generateGrpcWebRpcType(ctx, options.returnObservable, hasStreamingMethods));
      chunks.push(generateGrpcWebImpl(ctx, options.returnObservable, hasStreamingMethods));
      return (0, ts_poet_1.joinCode)(chunks, { on: "\n\n" });
    }
    function generateGrpcWebRpcType(ctx, returnObservable, hasStreamingMethods) {
      const chunks = [];
      const { options } = ctx;
      const { useAbortSignal } = options;
      chunks.push((0, ts_poet_1.code)`interface Rpc {`);
      const wrapper = returnObservable ? (0, types_12.observableType)(ctx) : "Promise";
      chunks.push((0, ts_poet_1.code)`
    unary<T extends UnaryMethodDefinitionish>(
      methodDesc: T,
      request: any,
      metadata: grpc.Metadata | undefined,
      ${useAbortSignal ? "abortSignal?: AbortSignal," : ""}
    ): ${wrapper}<any>;
  `);
      if (hasStreamingMethods) {
        chunks.push((0, ts_poet_1.code)`
      invoke<T extends UnaryMethodDefinitionish>(
        methodDesc: T,
        request: any,
        metadata: grpc.Metadata | undefined,
        ${useAbortSignal ? "abortSignal?: AbortSignal," : ""}
      ): ${(0, types_12.observableType)(ctx)}<any>;
    `);
      }
      chunks.push((0, ts_poet_1.code)`}`);
      return (0, ts_poet_1.joinCode)(chunks, { on: "\n" });
    }
    function generateGrpcWebImpl(ctx, returnObservable, hasStreamingMethods) {
      const options = (0, ts_poet_1.code)`
    {
      transport?: grpc.TransportFactory,
      ${hasStreamingMethods ? "streamingTransport?: grpc.TransportFactory," : ``}
      debug?: boolean,
      metadata?: grpc.Metadata,
      upStreamRetryCodes?: number[],
    }
  `;
      const chunks = [];
      chunks.push((0, ts_poet_1.code)`
    export class GrpcWebImpl {
      private host: string;
      private options: ${options};
      
      constructor(host: string, options: ${options}) {
        this.host = host;
        this.options = options;
      }
  `);
      if (returnObservable) {
        chunks.push(createObservableUnaryMethod(ctx));
      } else {
        chunks.push(createPromiseUnaryMethod(ctx));
      }
      if (hasStreamingMethods) {
        chunks.push(createInvokeMethod(ctx));
      }
      chunks.push((0, ts_poet_1.code)`}`);
      return (0, ts_poet_1.joinCode)(chunks, { trim: false });
    }
    function createPromiseUnaryMethod(ctx) {
      const { options } = ctx;
      const { useAbortSignal } = options;
      const maybeAbortSignal = useAbortSignal ? `
      if (abortSignal) abortSignal.addEventListener("abort", () => {
        client.close();
        reject(abortSignal.reason);
      });` : "";
      return (0, ts_poet_1.code)`
    unary<T extends UnaryMethodDefinitionish>(
      methodDesc: T,
      _request: any,
      metadata: grpc.Metadata | undefined,
      ${useAbortSignal ? "abortSignal?: AbortSignal," : ""}
    ): Promise<any> {
      const request = { ..._request, ...methodDesc.requestType };
      const maybeCombinedMetadata = metadata && this.options.metadata
        ? new ${BrowserHeaders}({ ...this.options?.metadata.headersMap, ...metadata?.headersMap })
        : metadata ?? this.options.metadata;
      return new Promise((resolve, reject) => {
        ${useAbortSignal ? `const client =` : ""} ${grpc}.unary(methodDesc, {
          request,
          host: this.host,
          metadata: maybeCombinedMetadata ?? {},
          ...(this.options.transport !== undefined ? {transport: this.options.transport} : {}),
          debug: this.options.debug ?? false,
          onEnd: function (response) {
            if (response.status === grpc.Code.OK) {
              resolve(response.message!.toObject());
            } else {
              const err = new ${ctx.utils.GrpcWebError}(response.statusMessage, response.status, response.trailers);
              reject(err);
            }
          },
        });

        ${maybeAbortSignal}
      });
    }
  `;
    }
    function createObservableUnaryMethod(ctx) {
      const { options } = ctx;
      const { useAbortSignal } = options;
      const maybeAbortSignal = useAbortSignal ? `
      if (abortSignal) abortSignal.addEventListener("abort", () => {
        observer.error(abortSignal.reason);
        client.close();
      });` : "";
      return (0, ts_poet_1.code)`
    unary<T extends UnaryMethodDefinitionish>(
      methodDesc: T,
      _request: any,
      metadata: grpc.Metadata | undefined,
      ${useAbortSignal ? "abortSignal?: AbortSignal," : ""}
    ): ${(0, types_12.observableType)(ctx)}<any> {
      const request = { ..._request, ...methodDesc.requestType };
      const maybeCombinedMetadata = metadata && this.options.metadata
        ? new ${BrowserHeaders}({ ...this.options?.metadata.headersMap, ...metadata?.headersMap })
        : metadata ?? this.options.metadata;
      return new Observable(observer => {
        ${useAbortSignal ? `const client =` : ""} ${grpc}.unary(methodDesc, {
          request,
          host: this.host,
          metadata: maybeCombinedMetadata ?? {},
          ...(this.options.transport !== undefined ? {transport: this.options.transport} : {}),
          debug: this.options.debug ?? false,
          onEnd: (next) => {
            if (next.status !== 0) {
              const err = new ${ctx.utils.GrpcWebError}(next.statusMessage, next.status, next.trailers);
              observer.error(err);
            } else {
              observer.next(next.message as any);
              observer.complete();
            }
          },
        });


      ${maybeAbortSignal}

      }).pipe(${take}(1));
    } 
  `;
    }
    function createInvokeMethod(ctx) {
      const { options } = ctx;
      const { useAbortSignal } = options;
      return (0, ts_poet_1.code)`
    invoke<T extends UnaryMethodDefinitionish>(
      methodDesc: T,
      _request: any,
      metadata: grpc.Metadata | undefined,
      ${useAbortSignal ? "abortSignal?: AbortSignal," : ""}
    ): ${(0, types_12.observableType)(ctx)}<any> {
      const upStreamCodes = this.options.upStreamRetryCodes ?? [];
      const DEFAULT_TIMEOUT_TIME: number = 3_000;
      const request = { ..._request, ...methodDesc.requestType };
      const transport = this.options.streamingTransport ?? this.options.transport;
      const maybeCombinedMetadata = metadata && this.options.metadata
        ? new ${BrowserHeaders}({ ...this.options?.metadata.headersMap, ...metadata?.headersMap })
        : metadata ?? this.options.metadata;
      return new Observable(observer => {
        const upStream = (() => {
          const client = ${grpc}.invoke(methodDesc, {
            host: this.host,
            request,
            ...(transport !== undefined ? {transport} : {}),
            metadata: maybeCombinedMetadata ?? {},
            debug: this.options.debug ?? false,
            onMessage: (next) => observer.next(next),
            onEnd: (code: ${grpc}.Code, message: string, trailers: ${grpc}.Metadata) => {
              if (code === 0) {
                observer.complete();
              } else if (upStreamCodes.includes(code)) {
                setTimeout(upStream, DEFAULT_TIMEOUT_TIME);
              } else {
                const err = new Error(message) as any;
                err.code = code;
                err.metadata = trailers;
                observer.error(err);
              }
            },
          });
          ${useAbortSignal ? `
          if (abortSignal) {
            const abort = () => {
              observer.error(abortSignal.reason);
              client.close();
            };
            abortSignal.addEventListener("abort", abort);
            observer.add(() => {
              if (abortSignal.aborted) {
                return;
              }

              abortSignal.removeEventListener('abort', abort); 
              client.close();
            });
          } else {
            observer.add(() => client.close());
          }
          ` : `observer.add(() => client.close());`}
        });
        upStream();
      }).pipe(${share}());
    }
  `;
    }
  }
});

// hardware/generic/goldfish/emulator/ui/aquarium/node_modules/ts-proto/build/src/generate-nestjs.js
var require_generate_nestjs = __commonJS({
  "hardware/generic/goldfish/emulator/ui/aquarium/node_modules/ts-proto/build/src/generate-nestjs.js"(exports2) {
    "use strict";
    Object.defineProperty(exports2, "__esModule", { value: true });
    exports2.generateNestjsServiceController = generateNestjsServiceController;
    exports2.generateNestjsServiceClient = generateNestjsServiceClient;
    exports2.generateNestjsGrpcServiceMethodsDecorator = generateNestjsGrpcServiceMethodsDecorator;
    var ts_poet_1 = require_build();
    var types_12 = require_types();
    var sourceInfo_1 = require_sourceInfo();
    var main_12 = require_main();
    var utils_12 = require_utils2();
    function generateNestjsServiceController(ctx, fileDesc, sourceInfo, serviceDesc) {
      const { options } = ctx;
      const chunks = [];
      const Metadata = (0, ts_poet_1.imp)("t:Metadata@@grpc/grpc-js");
      (0, utils_12.maybeAddComment)(options, sourceInfo, chunks, serviceDesc.options?.deprecated);
      const t = options.context ? `<${main_12.contextTypeVar}>` : "";
      chunks.push((0, ts_poet_1.code)`
    export interface ${serviceDesc.name}Controller${t} {
  `);
      serviceDesc.method.forEach((methodDesc, index) => {
        (0, utils_12.assertInstanceOf)(methodDesc, utils_12.FormattedMethodDescriptor);
        const info = sourceInfo.lookup(sourceInfo_1.Fields.service.method, index);
        (0, utils_12.maybeAddComment)(options, info, chunks, serviceDesc.options?.deprecated);
        const params = [];
        if (options.context) {
          params.push((0, ts_poet_1.code)`ctx: Context`);
        }
        params.push((0, ts_poet_1.code)`request: ${(0, types_12.requestType)(ctx, methodDesc)}`);
        if (options.addGrpcMetadata) {
          const q = options.addNestjsRestParameter ? "" : "?";
          params.push((0, ts_poet_1.code)`metadata${q}: ${Metadata}`);
        }
        if (options.addNestjsRestParameter) {
          params.push((0, ts_poet_1.code)`...rest: any`);
        }
        let returns;
        if ((0, types_12.isEmptyType)(methodDesc.outputType)) {
          returns = (0, ts_poet_1.code)`void | Promise<void>`;
        } else if (options.returnObservable || methodDesc.serverStreaming) {
          returns = (0, ts_poet_1.code)`${(0, types_12.responseObservable)(ctx, methodDesc)}`;
        } else {
          returns = (0, ts_poet_1.code)`
        ${(0, types_12.responsePromise)(ctx, methodDesc)}
        | ${(0, types_12.responseObservable)(ctx, methodDesc)}
        | ${(0, types_12.responseType)(ctx, methodDesc)}
      `;
        }
        chunks.push((0, ts_poet_1.code)`
      ${methodDesc.formattedName}(${(0, ts_poet_1.joinCode)(params, { on: ", " })}): ${returns};
    `);
        if (options.context) {
          const batchMethod = (0, types_12.detectBatchMethod)(ctx, fileDesc, serviceDesc, methodDesc);
          if (batchMethod) {
            const maybeCtx = options.context ? "ctx: Context," : "";
            chunks.push((0, ts_poet_1.code)`
          ${batchMethod.singleMethodName}(
            ${maybeCtx}
            ${(0, utils_12.singular)(batchMethod.inputFieldName)}: ${batchMethod.inputType},
          ): Promise<${batchMethod.outputType}>;
        `);
          }
        }
      });
      chunks.push((0, ts_poet_1.code)`}`);
      return (0, ts_poet_1.joinCode)(chunks, { on: "\n\n" });
    }
    function generateNestjsServiceClient(ctx, fileDesc, sourceInfo, serviceDesc) {
      const { options } = ctx;
      const chunks = [];
      const Metadata = (0, ts_poet_1.imp)("t:Metadata@@grpc/grpc-js");
      (0, utils_12.maybeAddComment)(options, sourceInfo, chunks);
      const t = options.context ? `<${main_12.contextTypeVar}>` : ``;
      chunks.push((0, ts_poet_1.code)`
    export interface ${serviceDesc.name}Client${t} {
  `);
      serviceDesc.method.forEach((methodDesc, index) => {
        (0, utils_12.assertInstanceOf)(methodDesc, utils_12.FormattedMethodDescriptor);
        const params = [];
        if (options.context) {
          params.push((0, ts_poet_1.code)`ctx: Context`);
        }
        params.push((0, ts_poet_1.code)`request: ${(0, types_12.requestType)(ctx, methodDesc)}`);
        if (options.addGrpcMetadata) {
          const q = options.addNestjsRestParameter ? "" : "?";
          params.push((0, ts_poet_1.code)`metadata${q}: ${Metadata}`);
        }
        if (options.addNestjsRestParameter) {
          params.push((0, ts_poet_1.code)`...rest: any`);
        }
        const returns = (0, types_12.responseObservable)(ctx, methodDesc);
        const info = sourceInfo.lookup(sourceInfo_1.Fields.service.method, index);
        (0, utils_12.maybeAddComment)(options, info, chunks, methodDesc.options?.deprecated);
        chunks.push((0, ts_poet_1.code)`
      ${methodDesc.formattedName}(
        ${(0, ts_poet_1.joinCode)(params, { on: "," })}
      ): ${returns};
    `);
        if (options.context) {
          const batchMethod = (0, types_12.detectBatchMethod)(ctx, fileDesc, serviceDesc, methodDesc);
          if (batchMethod) {
            const maybeContext = options.context ? `ctx: Context,` : "";
            chunks.push((0, ts_poet_1.code)`
          ${batchMethod.singleMethodName}(
            ${maybeContext}
            ${(0, utils_12.singular)(batchMethod.inputFieldName)}
          ): Promise<${batchMethod.inputType}>;
        `);
          }
        }
      });
      chunks.push((0, ts_poet_1.code)`}`);
      return (0, ts_poet_1.joinCode)(chunks, { on: "\n\n" });
    }
    function generateNestjsGrpcServiceMethodsDecorator(ctx, serviceDesc) {
      const { options } = ctx;
      const GrpcMethod = (0, ts_poet_1.imp)("GrpcMethod@@nestjs/microservices");
      const GrpcStreamMethod = (0, ts_poet_1.imp)("GrpcStreamMethod@@nestjs/microservices");
      const grpcMethods = serviceDesc.method.filter((m) => !m.clientStreaming).map((m) => {
        (0, utils_12.assertInstanceOf)(m, utils_12.FormattedMethodDescriptor);
        return m.formattedName;
      }).map((n) => `"${n}"`);
      const grpcStreamMethods = serviceDesc.method.filter((m) => m.clientStreaming).map((m) => {
        (0, utils_12.assertInstanceOf)(m, utils_12.FormattedMethodDescriptor);
        return m.formattedName;
      }).map((n) => `"${n}"`);
      return (0, ts_poet_1.code)`
    export function ${serviceDesc.name}ControllerMethods() {
      return function(constructor: Function) {
        const grpcMethods: string[] = [${grpcMethods.join(", ")}];
        for (const method of grpcMethods) {
          const descriptor: any = Reflect.getOwnPropertyDescriptor(constructor.prototype, method);
          ${GrpcMethod}('${serviceDesc.name}', method)(constructor.prototype[method], method, descriptor);
        }
        const grpcStreamMethods: string[] = [${grpcStreamMethods.join(", ")}];
        for (const method of grpcStreamMethods) {
          const descriptor: any = Reflect.getOwnPropertyDescriptor(constructor.prototype, method);
          ${GrpcStreamMethod}('${serviceDesc.name}', method)(constructor.prototype[method], method, descriptor);
        }
      };
    }
  `;
    }
  }
});

// hardware/generic/goldfish/emulator/ui/aquarium/node_modules/ts-proto/build/src/generate-nice-grpc.js
var require_generate_nice_grpc = __commonJS({
  "hardware/generic/goldfish/emulator/ui/aquarium/node_modules/ts-proto/build/src/generate-nice-grpc.js"(exports2) {
    "use strict";
    Object.defineProperty(exports2, "__esModule", { value: true });
    exports2.generateNiceGrpcService = generateNiceGrpcService;
    var ts_poet_1 = require_build();
    var case_1 = require_case();
    var sourceInfo_1 = require_sourceInfo();
    var types_12 = require_types();
    var utils_12 = require_utils2();
    var CallOptions = (0, ts_poet_1.imp)("t:CallOptions@nice-grpc-common");
    var CallContext = (0, ts_poet_1.imp)("t:CallContext@nice-grpc-common");
    function generateNiceGrpcService(ctx, fileDesc, sourceInfo, serviceDesc) {
      const chunks = [];
      chunks.push(generateServerStub(ctx, sourceInfo, serviceDesc));
      chunks.push(generateClientStub(ctx, sourceInfo, serviceDesc));
      return (0, ts_poet_1.joinCode)(chunks, { on: "\n\n" });
    }
    function generateServerStub(ctx, sourceInfo, serviceDesc) {
      const chunks = [];
      const maybeSuffix = serviceDesc.name.endsWith("Service") ? "" : "Service";
      chunks.push((0, ts_poet_1.code)`export interface ${(0, ts_poet_1.def)(`${serviceDesc.name}${maybeSuffix}Implementation`)}<CallContextExt = {}> {`);
      for (const [index, methodDesc] of serviceDesc.method.entries()) {
        (0, utils_12.assertInstanceOf)(methodDesc, utils_12.FormattedMethodDescriptor);
        const inputType = (0, types_12.messageToTypeName)(ctx, methodDesc.inputType, { keepValueType: true });
        let outputType = (0, types_12.messageToTypeName)(ctx, methodDesc.outputType, { keepValueType: true });
        if (ctx.options.outputPartialMethods) {
          outputType = (0, ts_poet_1.code)`${ctx.utils.DeepPartial}<${outputType}>`;
        }
        const ServerStreamingMethodResult = ctx.utils.NiceGrpcServerStreamingMethodResult;
        const info = sourceInfo.lookup(sourceInfo_1.Fields.service.method, index);
        (0, utils_12.maybeAddComment)(ctx.options, info, chunks, methodDesc.options?.deprecated);
        if (methodDesc.clientStreaming) {
          if (methodDesc.serverStreaming) {
            chunks.push((0, ts_poet_1.code)`
          ${(0, case_1.uncapitalize)(methodDesc.name)}(
            request: AsyncIterable<${inputType}>,
            context: ${CallContext} & CallContextExt,
          ): ${ServerStreamingMethodResult}<${outputType}>;
        `);
          } else {
            chunks.push((0, ts_poet_1.code)`
          ${(0, case_1.uncapitalize)(methodDesc.name)}(
            request: AsyncIterable<${inputType}>,
            context: ${CallContext} & CallContextExt,
          ): Promise<${outputType}>;
        `);
          }
        } else {
          if (methodDesc.serverStreaming) {
            chunks.push((0, ts_poet_1.code)`
          ${(0, case_1.uncapitalize)(methodDesc.name)}(
            request: ${inputType},
            context: ${CallContext} & CallContextExt,
          ): ${ServerStreamingMethodResult}<${outputType}>;
        `);
          } else {
            chunks.push((0, ts_poet_1.code)`
          ${(0, case_1.uncapitalize)(methodDesc.name)}(
            request: ${inputType},
            context: ${CallContext} & CallContextExt,
          ): Promise<${outputType}>;
        `);
          }
        }
      }
      chunks.push((0, ts_poet_1.code)`}`);
      return (0, ts_poet_1.joinCode)(chunks, { on: "\n" });
    }
    function generateClientStub(ctx, sourceInfo, serviceDesc) {
      const chunks = [];
      chunks.push((0, ts_poet_1.code)`export interface ${(0, ts_poet_1.def)(`${serviceDesc.name}Client`)}<CallOptionsExt = {}> {`);
      for (const [index, methodDesc] of serviceDesc.method.entries()) {
        (0, utils_12.assertInstanceOf)(methodDesc, utils_12.FormattedMethodDescriptor);
        let inputType = (0, types_12.messageToTypeName)(ctx, methodDesc.inputType, { keepValueType: true });
        if (ctx.options.outputPartialMethods) {
          inputType = (0, ts_poet_1.code)`${ctx.utils.DeepPartial}<${inputType}>`;
        }
        const outputType = (0, types_12.messageToTypeName)(ctx, methodDesc.outputType, { keepValueType: true });
        const info = sourceInfo.lookup(sourceInfo_1.Fields.service.method, index);
        (0, utils_12.maybeAddComment)(ctx.options, info, chunks, methodDesc.options?.deprecated);
        if (methodDesc.clientStreaming) {
          if (methodDesc.serverStreaming) {
            chunks.push((0, ts_poet_1.code)`
          ${(0, case_1.uncapitalize)(methodDesc.name)}(
            request: AsyncIterable<${inputType}>,
            options?: ${CallOptions} & CallOptionsExt,
          ): AsyncIterable<${outputType}>;
        `);
          } else {
            chunks.push((0, ts_poet_1.code)`
          ${(0, case_1.uncapitalize)(methodDesc.name)}(
            request: AsyncIterable<${inputType}>,
            options?: ${CallOptions} & CallOptionsExt,
          ): Promise<${outputType}>;
        `);
          }
        } else {
          if (methodDesc.serverStreaming) {
            chunks.push((0, ts_poet_1.code)`
          ${(0, case_1.uncapitalize)(methodDesc.name)}(
            request: ${inputType},
            options?: ${CallOptions} & CallOptionsExt,
          ): AsyncIterable<${outputType}>;
        `);
          } else {
            chunks.push((0, ts_poet_1.code)`
          ${(0, case_1.uncapitalize)(methodDesc.name)}(
            request: ${inputType},
            options?: ${CallOptions} & CallOptionsExt,
          ): Promise<${outputType}>;
        `);
          }
        }
      }
      chunks.push((0, ts_poet_1.code)`}`);
      return (0, ts_poet_1.joinCode)(chunks, { on: "\n" });
    }
  }
});

// hardware/generic/goldfish/emulator/ui/aquarium/node_modules/ts-proto/build/src/generate-services.js
var require_generate_services = __commonJS({
  "hardware/generic/goldfish/emulator/ui/aquarium/node_modules/ts-proto/build/src/generate-services.js"(exports2) {
    "use strict";
    Object.defineProperty(exports2, "__esModule", { value: true });
    exports2.generateService = generateService;
    exports2.generateServiceClientImpl = generateServiceClientImpl;
    exports2.generateRpcType = generateRpcType;
    exports2.generateDataLoadersType = generateDataLoadersType;
    exports2.generateDataLoaderOptionsType = generateDataLoaderOptionsType;
    var ts_poet_1 = require_build();
    var types_12 = require_types();
    var utils_12 = require_utils2();
    var sourceInfo_1 = require_sourceInfo();
    var main_12 = require_main();
    function generateService(ctx, fileDesc, sourceInfo, serviceDesc) {
      const { options } = ctx;
      const chunks = [];
      (0, utils_12.maybeAddComment)(options, sourceInfo, chunks, serviceDesc.options?.deprecated);
      const maybeTypeVar = options.context ? `<${main_12.contextTypeVar}>` : "";
      chunks.push((0, ts_poet_1.code)`export interface ${(0, ts_poet_1.def)(serviceDesc.name)}${maybeTypeVar} {`);
      serviceDesc.method.forEach((methodDesc, index) => {
        (0, utils_12.assertInstanceOf)(methodDesc, utils_12.FormattedMethodDescriptor);
        const info = sourceInfo.lookup(sourceInfo_1.Fields.service.method, index);
        (0, utils_12.maybeAddComment)(options, info, chunks, methodDesc.options?.deprecated);
        const params = [];
        if (options.context) {
          params.push((0, ts_poet_1.code)`ctx: Context`);
        }
        const partialInput = options.outputClientImpl === "grpc-web";
        const inputType = (0, types_12.requestType)(ctx, methodDesc, partialInput);
        params.push((0, ts_poet_1.code)`request: ${inputType}`);
        if (options.outputClientImpl === "grpc-web") {
          params.push((0, ts_poet_1.code)`metadata?: grpc.Metadata`);
        } else if (options.metadataType) {
          const Metadata = (0, ts_poet_1.imp)(options.metadataType);
          params.push((0, ts_poet_1.code)`metadata?: ${Metadata}`);
        } else if (options.addGrpcMetadata) {
          const Metadata = (0, ts_poet_1.imp)("t:Metadata@@grpc/grpc-js");
          params.push((0, ts_poet_1.code)`metadata?: ${Metadata}`);
        }
        if (options.useAbortSignal) {
          params.push((0, ts_poet_1.code)`abortSignal?: AbortSignal`);
        }
        if (options.addNestjsRestParameter) {
          params.push((0, ts_poet_1.code)`...rest: any`);
        }
        chunks.push((0, ts_poet_1.code)`${methodDesc.formattedName}(${(0, ts_poet_1.joinCode)(params, { on: "," })}): ${(0, types_12.responsePromiseOrObservable)(ctx, methodDesc)};`);
        if (options.context) {
          const batchMethod = (0, types_12.detectBatchMethod)(ctx, fileDesc, serviceDesc, methodDesc);
          if (batchMethod) {
            chunks.push((0, ts_poet_1.code)`${batchMethod.singleMethodName}(
          ctx: Context,
          ${(0, utils_12.singular)(batchMethod.inputFieldName)}: ${batchMethod.inputType},
        ): Promise<${batchMethod.outputType}>;`);
          }
        }
      });
      chunks.push((0, ts_poet_1.code)`}`);
      return (0, ts_poet_1.joinCode)(chunks, { on: "\n" });
    }
    function generateRegularRpcMethod(ctx, methodDesc) {
      (0, utils_12.assertInstanceOf)(methodDesc, utils_12.FormattedMethodDescriptor);
      const { options } = ctx;
      const BinaryReader = (0, ts_poet_1.imp)("BinaryReader@@bufbuild/protobuf/wire");
      const rawInputType = (0, types_12.rawRequestType)(ctx, methodDesc, { keepValueType: true });
      const inputType = (0, types_12.requestType)(ctx, methodDesc);
      const rawOutputType = (0, types_12.responseType)(ctx, methodDesc, { keepValueType: true });
      const metadataType = options.metadataType ? (0, ts_poet_1.imp)(options.metadataType) : (0, ts_poet_1.imp)("t:Metadata@@grpc/grpc-js");
      const params = [
        ...options.context ? [(0, ts_poet_1.code)`ctx: Context`] : [],
        (0, ts_poet_1.code)`request: ${inputType}`,
        ...options.metadataType || options.addGrpcMetadata ? [(0, ts_poet_1.code)`metadata?: ${metadataType}`] : [],
        ...options.useAbortSignal ? [(0, ts_poet_1.code)`abortSignal?: AbortSignal`] : []
      ];
      const maybeCtx = options.context ? "ctx," : "";
      const maybeMetadata = options.addGrpcMetadata ? "metadata," : "";
      const maybeAbortSignal = options.useAbortSignal ? "abortSignal || undefined," : "";
      let errorHandler;
      if (options.rpcErrorHandler) {
        errorHandler = (0, ts_poet_1.code)`
      if (this.rpc.handleError) {
        return Promise.reject(this.rpc.handleError(this.service, "${methodDesc.name}", error));
      }
      return Promise.reject(error);
    `;
      }
      let encode = (0, ts_poet_1.code)`${rawInputType}.encode(request).finish()`;
      let beforeRequest;
      if (options.rpcBeforeRequest && !methodDesc.clientStreaming) {
        beforeRequest = generateBeforeRequest(methodDesc.name);
      } else if (methodDesc.clientStreaming && options.rpcBeforeRequest) {
        encode = (0, ts_poet_1.code)`{const encodedRequest = ${encode}; ${generateBeforeRequest(methodDesc.name, "encodedRequest")}; return encodedRequest}`;
      }
      let decode = (0, ts_poet_1.code)`${rawOutputType}.decode(new ${BinaryReader}(data))`;
      if (options.rpcAfterResponse) {
        decode = (0, ts_poet_1.code)`
      const response = ${rawOutputType}.decode(new ${BinaryReader}(data));
      if (this.rpc.afterResponse) {
        this.rpc.afterResponse(this.service, "${methodDesc.name}", response);
      }
      return response;
    `;
      }
      if (methodDesc.clientStreaming) {
        if (options.useAsyncIterable) {
          encode = (0, ts_poet_1.code)`${rawInputType}.encodeTransform(request)`;
        } else {
          encode = (0, ts_poet_1.code)`request.pipe(${(0, ts_poet_1.imp)("map@rxjs/operators")}(request => ${encode}))`;
        }
      }
      const returnStatement = createDefaultServiceReturn(ctx, methodDesc, decode, errorHandler);
      let returnVariable;
      if (options.returnObservable || methodDesc.serverStreaming) {
        returnVariable = "result";
      } else {
        returnVariable = "promise";
      }
      let rpcMethod;
      if (methodDesc.clientStreaming && methodDesc.serverStreaming) {
        rpcMethod = "bidirectionalStreamingRequest";
      } else if (methodDesc.serverStreaming) {
        rpcMethod = "serverStreamingRequest";
      } else if (methodDesc.clientStreaming) {
        rpcMethod = "clientStreamingRequest";
      } else {
        rpcMethod = "request";
      }
      return (0, ts_poet_1.code)`
    ${methodDesc.formattedName}(
      ${(0, ts_poet_1.joinCode)(params, { on: "," })}
    ): ${(0, types_12.responsePromiseOrObservable)(ctx, methodDesc)} {
      const data = ${encode}; ${beforeRequest ? beforeRequest : ""}
      const ${returnVariable} = this.rpc.${rpcMethod}(
        ${maybeCtx}
        this.service,
        "${methodDesc.name}",
        data,
        ${maybeMetadata}
        ${maybeAbortSignal}
      );
      return ${returnStatement};
    }
  `;
    }
    function generateBeforeRequest(methodName, requestVariableName = "request") {
      return (0, ts_poet_1.code)`
    if (this.rpc.beforeRequest) {
      this.rpc.beforeRequest(this.service, "${methodName}", ${requestVariableName});
    }`;
    }
    function createDefaultServiceReturn(ctx, methodDesc, decode, errorHandler) {
      const { options } = ctx;
      const rawOutputType = (0, types_12.responseType)(ctx, methodDesc, { keepValueType: true });
      const returnStatement = (0, utils_12.arrowFunction)("data", decode, !options.rpcAfterResponse);
      if (options.returnObservable || methodDesc.serverStreaming) {
        if (options.useAsyncIterable) {
          return (0, ts_poet_1.code)`${rawOutputType}.decodeTransform(result)`;
        } else {
          if (errorHandler) {
            const tc = (0, utils_12.arrowFunction)("data", (0, utils_12.tryCatchBlock)(decode, (0, ts_poet_1.code)`throw error`), !options.rpcAfterResponse);
            return (0, ts_poet_1.code)`result.pipe(${(0, ts_poet_1.imp)("map@rxjs/operators")}(${tc}))`;
          }
          return (0, ts_poet_1.code)`result.pipe(${(0, ts_poet_1.imp)("map@rxjs/operators")}(${returnStatement}))`;
        }
      }
      if (errorHandler) {
        if (!options.rpcAfterResponse) {
          decode = (0, ts_poet_1.code)`return ${decode}`;
        }
        return (0, ts_poet_1.code)`promise.then(${(0, utils_12.arrowFunction)("data", (0, utils_12.tryCatchBlock)(decode, (0, ts_poet_1.code)`return Promise.reject(error);`), false)}).catch(${(0, utils_12.arrowFunction)("error", errorHandler, false)})`;
      }
      return (0, ts_poet_1.code)`promise.then(${returnStatement})`;
    }
    function generateServiceClientImpl(ctx, fileDesc, serviceDesc) {
      const { options } = ctx;
      const chunks = [];
      const { name } = serviceDesc;
      const serviceName = (0, utils_12.maybePrefixPackage)(fileDesc, serviceDesc.name);
      const serviceNameConst = `${name}ServiceName`;
      chunks.push((0, ts_poet_1.code)`export const ${serviceNameConst} = "${serviceName}";`);
      const i = options.context ? `${name}<Context>` : name;
      const t = options.context ? `<${main_12.contextTypeVar}>` : "";
      chunks.push((0, ts_poet_1.code)`export class ${name}ClientImpl${t} implements ${(0, ts_poet_1.def)(i)} {`);
      const rpcType = options.context ? "Rpc<Context>" : "Rpc";
      chunks.push((0, ts_poet_1.code)`private readonly rpc: ${rpcType};`);
      chunks.push((0, ts_poet_1.code)`private readonly service: string;`);
      chunks.push((0, ts_poet_1.code)`constructor(rpc: ${rpcType}, opts?: {service?: string}) {`);
      chunks.push((0, ts_poet_1.code)`this.service = opts?.service || ${serviceNameConst};`);
      chunks.push((0, ts_poet_1.code)`this.rpc = rpc;`);
      for (const methodDesc of serviceDesc.method) {
        (0, utils_12.assertInstanceOf)(methodDesc, utils_12.FormattedMethodDescriptor);
        chunks.push((0, ts_poet_1.code)`this.${methodDesc.formattedName} = this.${methodDesc.formattedName}.bind(this);`);
      }
      chunks.push((0, ts_poet_1.code)`}`);
      for (const methodDesc of serviceDesc.method) {
        if (options.context) {
          const batchMethod = (0, types_12.detectBatchMethod)(ctx, fileDesc, serviceDesc, methodDesc);
          if (batchMethod) {
            chunks.push(generateBatchingRpcMethod(ctx, batchMethod));
          }
        }
        if (options.context && methodDesc.name.match(/^Get[A-Z]/) && !methodDesc.serverStreaming && !methodDesc.clientStreaming) {
          chunks.push(generateCachingRpcMethod(ctx, fileDesc, serviceDesc, methodDesc));
        } else {
          chunks.push(generateRegularRpcMethod(ctx, methodDesc));
        }
      }
      chunks.push((0, ts_poet_1.code)`}`);
      return (0, ts_poet_1.code)`${chunks}`;
    }
    function generateBatchingRpcMethod(ctx, batchMethod) {
      const { methodDesc, singleMethodName, inputFieldName, inputType, outputFieldName, outputType, mapType, uniqueIdentifier } = batchMethod;
      (0, utils_12.assertInstanceOf)(methodDesc, utils_12.FormattedMethodDescriptor);
      const { options } = ctx;
      const hash = options.esModuleInterop ? (0, ts_poet_1.imp)("hash=object-hash") : (0, ts_poet_1.imp)("hash*object-hash");
      const dataloader = options.esModuleInterop ? (0, ts_poet_1.imp)("DataLoader=dataloader") : (0, ts_poet_1.imp)("DataLoader*dataloader");
      const lambda = [];
      lambda.push((0, ts_poet_1.code)`
    (${inputFieldName}) => {
      const request = { ${inputFieldName} };
  `);
      if (mapType) {
        lambda.push((0, ts_poet_1.code)`
      return this.${methodDesc.formattedName}(ctx, request as any).then(res => {
        return ${inputFieldName}.map(key => res.${outputFieldName}[key] ?? ${ctx.utils.fail}())
      });
    `);
      } else {
        lambda.push((0, ts_poet_1.code)`
      return this.${methodDesc.formattedName}(ctx, request as any).then(res => res.${outputFieldName})
    `);
      }
      lambda.push((0, ts_poet_1.code)`}`);
      return (0, ts_poet_1.code)`
    ${singleMethodName}(
      ctx: Context,
      ${(0, utils_12.singular)(inputFieldName)}: ${inputType}
    ): Promise<${outputType}> {
      const dl = ctx.getDataLoader("${uniqueIdentifier}", () => {
        return new ${dataloader}<${inputType}, ${outputType}, string>(
          ${(0, ts_poet_1.joinCode)(lambda)},
          { cacheKeyFn: ${hash}, ...ctx.rpcDataLoaderOptions }
        );
      });
      return dl.load(${(0, utils_12.singular)(inputFieldName)});
    }
  `;
    }
    function generateCachingRpcMethod(ctx, fileDesc, serviceDesc, methodDesc) {
      (0, utils_12.assertInstanceOf)(methodDesc, utils_12.FormattedMethodDescriptor);
      const { options } = ctx;
      const hash = options.esModuleInterop ? (0, ts_poet_1.imp)("hash=object-hash") : (0, ts_poet_1.imp)("hash*object-hash");
      const dataloader = options.esModuleInterop ? (0, ts_poet_1.imp)("DataLoader=dataloader") : (0, ts_poet_1.imp)("DataLoader*dataloader");
      const inputType = (0, types_12.requestType)(ctx, methodDesc);
      const outputType = (0, types_12.responseType)(ctx, methodDesc);
      const uniqueIdentifier = `${(0, utils_12.maybePrefixPackage)(fileDesc, serviceDesc.name)}.${methodDesc.name}`;
      const BinaryReader = (0, ts_poet_1.imp)("BinaryReader@@bufbuild/protobuf/wire");
      const lambda = (0, ts_poet_1.code)`
    (requests) => {
      const responses = requests.map(async request => {
        const data = ${inputType}.encode(request).finish()
        const response = await this.rpc.request(ctx, "${(0, utils_12.maybePrefixPackage)(fileDesc, serviceDesc.name)}", "${methodDesc.name}", data);
        return ${outputType}.decode(new ${BinaryReader}(response));
      });
      return Promise.all(responses);
    }
  `;
      return (0, ts_poet_1.code)`
    ${methodDesc.formattedName}(
      ctx: Context,
      request: ${inputType},
    ): Promise<${outputType}> {
      const dl = ctx.getDataLoader("${uniqueIdentifier}", () => {
        return new ${dataloader}<${inputType}, ${outputType}, string>(
          ${lambda},
          { cacheKeyFn: ${hash}, ...ctx.rpcDataLoaderOptions },
        );
      });
      return dl.load(request);
    }
  `;
    }
    function generateRpcType(ctx, hasStreamingMethods) {
      const { options } = ctx;
      const metadata = options.metadataType ? (0, ts_poet_1.imp)(options.metadataType) : (0, ts_poet_1.imp)("t:Metadata@@grpc/grpc-js");
      const metadataType = metadata.symbol;
      const maybeContext = options.context ? "<Context>" : "";
      const maybeContextParam = options.context ? "ctx: Context," : "";
      const maybeMetadataParam = options.metadataType || options.addGrpcMetadata ? `metadata?: ${metadataType},` : "";
      const maybeAbortSignalParam = options.useAbortSignal ? "abortSignal?: AbortSignal," : "";
      const methods = [[(0, ts_poet_1.code)`request`, (0, ts_poet_1.code)`Uint8Array`, (0, ts_poet_1.code)`Promise<Uint8Array>`]];
      const additionalMethods = [];
      if (options.rpcBeforeRequest) {
        additionalMethods.push((0, ts_poet_1.code)`beforeRequest?<T extends { [k in keyof T]: unknown }>(service: string, method: string, request: T): void;`);
      }
      if (options.rpcAfterResponse) {
        additionalMethods.push((0, ts_poet_1.code)`afterResponse?<T extends { [k in keyof T]: unknown }>(service: string, method: string, response: T): void;`);
      }
      if (options.rpcErrorHandler) {
        additionalMethods.push((0, ts_poet_1.code)`handleError?(service: string, method: string, error: globalThis.Error): globalThis.Error;`);
      }
      if (hasStreamingMethods) {
        const observable = (0, types_12.observableType)(ctx, true);
        methods.push([(0, ts_poet_1.code)`clientStreamingRequest`, (0, ts_poet_1.code)`${observable}<Uint8Array>`, (0, ts_poet_1.code)`Promise<Uint8Array>`]);
        methods.push([(0, ts_poet_1.code)`serverStreamingRequest`, (0, ts_poet_1.code)`Uint8Array`, (0, ts_poet_1.code)`${observable}<Uint8Array>`]);
        methods.push([
          (0, ts_poet_1.code)`bidirectionalStreamingRequest`,
          (0, ts_poet_1.code)`${observable}<Uint8Array>`,
          (0, ts_poet_1.code)`${observable}<Uint8Array>`
        ]);
      }
      const chunks = [];
      chunks.push((0, ts_poet_1.code)`    interface Rpc${maybeContext} {`);
      methods.forEach((method) => {
        chunks.push((0, ts_poet_1.code)`
      ${method[0]}(
        ${maybeContextParam}
        service: string,
        method: string,
        data: ${method[1]},
        ${maybeMetadataParam}
        ${maybeAbortSignalParam}
      ): ${method[2]};`);
      });
      additionalMethods.forEach((method) => chunks.push(method));
      chunks.push((0, ts_poet_1.code)`    }`);
      return (0, ts_poet_1.joinCode)(chunks, { on: "\n" });
    }
    function generateDataLoadersType() {
      return (0, ts_poet_1.code)`
    export interface DataLoaders {
      rpcDataLoaderOptions?: DataLoaderOptions;
      getDataLoader<T>(identifier: string, constructorFn: () => T): T;
    }
  `;
    }
    function generateDataLoaderOptionsType() {
      return (0, ts_poet_1.code)`
    export interface DataLoaderOptions {
      cache?: boolean;
    }
  `;
    }
  }
});

// hardware/generic/goldfish/emulator/ui/aquarium/node_modules/ts-proto/build/src/generate-struct-wrappers.js
var require_generate_struct_wrappers = __commonJS({
  "hardware/generic/goldfish/emulator/ui/aquarium/node_modules/ts-proto/build/src/generate-struct-wrappers.js"(exports2) {
    "use strict";
    Object.defineProperty(exports2, "__esModule", { value: true });
    exports2.isWrapperType = isWrapperType;
    exports2.generateWrapDeep = generateWrapDeep;
    exports2.generateUnwrapDeep = generateUnwrapDeep;
    exports2.generateWrapShallow = generateWrapShallow;
    exports2.generateUnwrapShallow = generateUnwrapShallow;
    var ts_poet_1 = require_build();
    var utils_12 = require_utils2();
    var types_12 = require_types();
    var options_12 = require_options();
    function isWrapperType(fullProtoTypeName) {
      return (0, types_12.isStructTypeName)(fullProtoTypeName) || (0, types_12.isAnyValueTypeName)(fullProtoTypeName) || (0, types_12.isListValueTypeName)(fullProtoTypeName) || (0, types_12.isFieldMaskTypeName)(fullProtoTypeName);
    }
    function generateWrapDeep(ctx, fullProtoTypeName, fieldNames) {
      const chunks = [];
      if ((0, types_12.isStructTypeName)(fullProtoTypeName)) {
        let setStatement = `struct.fields[key] = ${(0, utils_12.wrapTypeName)(ctx.options, "Value")}.wrap(object[key]);`;
        let defaultFields = "struct.fields ??= {};";
        if (ctx.options.useMapType) {
          setStatement = `struct.fields.set(key, ${(0, utils_12.wrapTypeName)(ctx.options, "Value")}.wrap(object[key]));`;
          defaultFields = "struct.fields ??= new Map<string, any | undefined>();";
        }
        if (ctx.options.useOptionals !== "all")
          defaultFields = "";
        chunks.push((0, ts_poet_1.code)`wrap(object: {[key: string]: any} | undefined): ${(0, utils_12.wrapTypeName)(ctx.options, "Struct")} {
      const struct = createBase${(0, utils_12.wrapTypeName)(ctx.options, "Struct")}();
      ${defaultFields}
      if (object !== undefined) {
        for (const key of ${ctx.utils.globalThis}.Object.keys(object)) {
          ${setStatement}
        }
      }
      return struct;
    }`);
      }
      if ((0, types_12.isAnyValueTypeName)(fullProtoTypeName)) {
        chunks.push((0, ts_poet_1.code)`wrap(value: any): ${(0, utils_12.wrapTypeName)(ctx.options, "Value")} {
      const result = {} as any;
      if (value === null) {
        result.${fieldNames.nullValue} = ${(0, utils_12.wrapTypeName)(ctx.options, "NullValue")}.NULL_VALUE;
      } else if (typeof value === 'boolean') {
        result.${fieldNames.boolValue} = value;
      } else if (typeof value === 'number') {
        result.${fieldNames.numberValue} = value;
      } else if (typeof value === 'string') {
        result.${fieldNames.stringValue} = value;
      } else if (${ctx.utils.globalThis}.Array.isArray(value)) {
        result.${fieldNames.listValue} = ${(0, utils_12.wrapTypeName)(ctx.options, "ListValue")}.wrap(value);
      } else if (typeof value === 'object') {
        result.${fieldNames.structValue} = ${(0, utils_12.wrapTypeName)(ctx.options, "Struct")}.wrap(value);
      } else if (typeof value !== 'undefined') {
        throw new ${ctx.utils.globalThis}.Error('Unsupported any value type: ' + typeof value);
      }
      return result;
    }`);
      }
      if ((0, types_12.isListValueTypeName)(fullProtoTypeName)) {
        const maybeReadyOnly = ctx.options.useReadonlyTypes ? "Readonly" : "";
        chunks.push((0, ts_poet_1.code)`wrap(array: ${maybeReadyOnly}Array<any> | undefined): ${(0, utils_12.wrapTypeName)(ctx.options, "ListValue")} {
      const result = createBase${(0, utils_12.wrapTypeName)(ctx.options, "ListValue")}()${maybeAsAny(ctx.options)};
      result.values = (array ?? []).map(${(0, utils_12.wrapTypeName)(ctx.options, "Value")}.wrap);
      return result;
    }`);
      }
      if ((0, types_12.isFieldMaskTypeName)(fullProtoTypeName)) {
        chunks.push((0, ts_poet_1.code)`wrap(paths: ${maybeReadonly(ctx.options)} string[]): ${(0, utils_12.wrapTypeName)(ctx.options, "FieldMask")} {
      const result = createBase${(0, utils_12.wrapTypeName)(ctx.options, "FieldMask")}()${maybeAsAny(ctx.options)};
      result.paths = paths;
      return result;
    }`);
      }
      return chunks;
    }
    function generateUnwrapDeep(ctx, fullProtoTypeName, fieldNames) {
      const chunks = [];
      if ((0, types_12.isStructTypeName)(fullProtoTypeName)) {
        if (ctx.options.useMapType) {
          chunks.push((0, ts_poet_1.code)`unwrap(message: ${(0, utils_12.wrapTypeName)(ctx.options, "Struct")}: {[key: string]: any} {
        const object: { [key: string]: any } = {};
        if (message.fields) {
          for (const key of message.fields.keys()) {
            object[key] = Value.unwrap(message.fields.get(key));
          }
        }
        return object;
      }`);
        } else {
          chunks.push((0, ts_poet_1.code)`unwrap(message: ${(0, utils_12.wrapTypeName)(ctx.options, "Struct")}): {[key: string]: any} {
        const object: { [key: string]: any } = {};
        if (message.fields) {
          for (const key of ${ctx.utils.globalThis}.Object.keys(message.fields)) {
            object[key] = Value.unwrap(message.fields[key]);
          }
        }
        return object;
      }`);
        }
      }
      if ((0, types_12.isAnyValueTypeName)(fullProtoTypeName)) {
        chunks.push((0, ts_poet_1.code)`unwrap(message: any): string | number | boolean | Object | null | Array<any> | undefined {
      if (message?.hasOwnProperty('${fieldNames.stringValue}') && message.${fieldNames.stringValue} !== undefined) {
        return message.${fieldNames.stringValue};
      } else if (message?.hasOwnProperty('${fieldNames.numberValue}') && message?.${fieldNames.numberValue} !== undefined) {
        return message.${fieldNames.numberValue};
      } else if (message?.hasOwnProperty('${fieldNames.boolValue}') && message?.${fieldNames.boolValue} !== undefined) {
        return message.${fieldNames.boolValue};
      } else if (message?.hasOwnProperty('${fieldNames.structValue}') && message?.${fieldNames.structValue} !== undefined) {
        return ${(0, utils_12.wrapTypeName)(ctx.options, "Struct")}.unwrap(message.${fieldNames.structValue} as any);
      } else if (message?.hasOwnProperty('${fieldNames.listValue}') && message?.${fieldNames.listValue} !== undefined) {
        return ${(0, utils_12.wrapTypeName)(ctx.options, "ListValue")}.unwrap(message.${fieldNames.listValue});
      } else if (message?.hasOwnProperty('${fieldNames.nullValue}') && message?.${fieldNames.nullValue} !== undefined) {
        return null;
      }
      return undefined;
    }`);
      }
      if ((0, types_12.isListValueTypeName)(fullProtoTypeName)) {
        chunks.push((0, ts_poet_1.code)`unwrap(message: ${ctx.options.useReadonlyTypes ? "any" : (0, utils_12.wrapTypeName)(ctx.options, "ListValue")}): Array<any> {
      if (message?.hasOwnProperty('values') && ${ctx.utils.globalThis}.Array.isArray(message.values)) {
        return message.values.map(Value.unwrap);
      } else {
        return message as any;
      }
    }`);
      }
      if ((0, types_12.isFieldMaskTypeName)(fullProtoTypeName)) {
        chunks.push(generateFieldMaskUnwrap(ctx));
      }
      return chunks;
    }
    function generateWrapShallow(ctx, fullProtoTypeName, fieldNames) {
      const chunks = [];
      if ((0, types_12.isStructTypeName)(fullProtoTypeName)) {
        let setStatement = "struct.fields[key] = object[key];";
        let defaultFields = "struct.fields ??= {};";
        if (ctx.options.useMapType) {
          setStatement = "struct.fields.set(key, object[key]);";
          defaultFields = "struct.fields ??= new Map<string, any | undefined>();";
        }
        if (ctx.options.useOptionals !== "all")
          defaultFields = "";
        chunks.push((0, ts_poet_1.code)`wrap(object: {[key: string]: any} | undefined): ${(0, utils_12.wrapTypeName)(ctx.options, "Struct")} {
      const struct = createBase${(0, utils_12.wrapTypeName)(ctx.options, "Struct")}();
      ${defaultFields}
      if (object !== undefined) {
        for (const key of ${ctx.utils.globalThis}.Object.keys(object)) {
          ${setStatement}
        }
      }
      return struct;
    }`);
      }
      if ((0, types_12.isAnyValueTypeName)(fullProtoTypeName)) {
        if (ctx.options.oneof === options_12.OneofOption.UNIONS) {
          chunks.push((0, ts_poet_1.code)`wrap(value: any): ${(0, utils_12.wrapTypeName)(ctx.options, "Value")} {
        const result = createBase${(0, utils_12.wrapTypeName)(ctx.options, "Value")}()${maybeAsAny(ctx.options)};
        if (value === null) {
          result.kind = {$case: '${fieldNames.nullValue}', ${fieldNames.nullValue}: ${(0, utils_12.wrapTypeName)(ctx.options, "NullValue")}.NULL_VALUE};
        } else if (typeof value === 'boolean') {
          result.kind = {$case: '${fieldNames.boolValue}', ${fieldNames.boolValue}: value};
        } else if (typeof value === 'number') {
          result.kind = {$case: '${fieldNames.numberValue}', ${fieldNames.numberValue}: value};
        } else if (typeof value === 'string') {
          result.kind = {$case: '${fieldNames.stringValue}', ${fieldNames.stringValue}: value};
        } else if (${ctx.utils.globalThis}.Array.isArray(value)) {
          result.kind = {$case: '${fieldNames.listValue}', ${fieldNames.listValue}: value};
        } else if (typeof value === 'object') {
          result.kind = {$case: '${fieldNames.structValue}', ${fieldNames.structValue}: value};
        } else if (typeof value !== 'undefined') {
          throw new ${ctx.utils.globalThis}.Error('Unsupported any value type: ' + typeof value);
        }
        return result;
    }`);
        } else if (ctx.options.oneof === options_12.OneofOption.UNIONS_VALUE) {
          chunks.push((0, ts_poet_1.code)`wrap(value: any): ${(0, utils_12.wrapTypeName)(ctx.options, "Value")} {
        const result = createBase${(0, utils_12.wrapTypeName)(ctx.options, "Value")}()${maybeAsAny(ctx.options)};
        if (value === null) {
          result.kind = {$case: '${fieldNames.nullValue}', value: ${(0, utils_12.wrapTypeName)(ctx.options, "NullValue")}.NULL_VALUE};
        } else if (typeof value === 'boolean') {
          result.kind = {$case: '${fieldNames.boolValue}', value };
        } else if (typeof value === 'number') {
          result.kind = {$case: '${fieldNames.numberValue}', value };
        } else if (typeof value === 'string') {
          result.kind = {$case: '${fieldNames.stringValue}', value };
        } else if (${ctx.utils.globalThis}.Array.isArray(value)) {
          result.kind = {$case: '${fieldNames.listValue}', value };
        } else if (typeof value === 'object') {
          result.kind = {$case: '${fieldNames.structValue}', value };
        } else if (typeof value !== 'undefined') {
          throw new ${ctx.utils.globalThis}.Error('Unsupported any value type: ' + typeof value);
        }
        return result;
    }`);
        } else {
          chunks.push((0, ts_poet_1.code)`wrap(value: any): ${(0, utils_12.wrapTypeName)(ctx.options, "Value")} {
        const result = createBase${(0, utils_12.wrapTypeName)(ctx.options, "Value")}()${maybeAsAny(ctx.options)};
        if (value === null) {
          result.${fieldNames.nullValue} = ${(0, utils_12.wrapTypeName)(ctx.options, "NullValue")}.NULL_VALUE;
        } else if (typeof value === 'boolean') {
          result.${fieldNames.boolValue} = value;
        } else if (typeof value === 'number') {
          result.${fieldNames.numberValue} = value;
        } else if (typeof value === 'string') {
          result.${fieldNames.stringValue} = value;
        } else if (${ctx.utils.globalThis}.Array.isArray(value)) {
          result.${fieldNames.listValue} = value;
        } else if (typeof value === 'object') {
          result.${fieldNames.structValue} = value;
        } else if (typeof value !== 'undefined') {
          throw new ${ctx.utils.globalThis}.Error('Unsupported any value type: ' + typeof value);
        }
        return result;
      }`);
        }
      }
      if ((0, types_12.isListValueTypeName)(fullProtoTypeName)) {
        const maybeReadyOnly = ctx.options.useReadonlyTypes ? "Readonly" : "";
        chunks.push((0, ts_poet_1.code)`wrap(array: ${maybeReadyOnly}Array<any> | undefined): ${(0, utils_12.wrapTypeName)(ctx.options, "ListValue")} {
      const result = createBase${(0, utils_12.wrapTypeName)(ctx.options, "ListValue")}()${maybeAsAny(ctx.options)};
      result.values = array ?? [];
      return result;
    }`);
      }
      if ((0, types_12.isFieldMaskTypeName)(fullProtoTypeName)) {
        chunks.push((0, ts_poet_1.code)`wrap(paths: ${maybeReadonly(ctx.options)} string[]): ${(0, utils_12.wrapTypeName)(ctx.options, "FieldMask")} {
      const result = createBase${(0, utils_12.wrapTypeName)(ctx.options, "FieldMask")}()${maybeAsAny(ctx.options)};
      result.paths = paths;
      return result;
    }`);
      }
      return chunks;
    }
    function generateUnwrapShallow(ctx, fullProtoTypeName, fieldNames) {
      const chunks = [];
      if ((0, types_12.isStructTypeName)(fullProtoTypeName)) {
        if (ctx.options.useMapType) {
          chunks.push((0, ts_poet_1.code)`unwrap(message: ${(0, utils_12.wrapTypeName)(ctx.options, "Struct")}): {[key: string]: any} {
        const object: { [key: string]: any } = {};
        if (message.fields) {
          for (const key of message.fields.keys()) {
            object[key] = message.fields.get(key);
          }
        }
        return object;
      }`);
        } else {
          chunks.push((0, ts_poet_1.code)`unwrap(message: ${(0, utils_12.wrapTypeName)(ctx.options, "Struct")}): {[key: string]: any} {
        const object: { [key: string]: any } = {};
        if (message.fields) {
          for (const key of ${ctx.utils.globalThis}.Object.keys(message.fields)) {
            object[key] = message.fields[key];
          }
        }
        return object;
      }`);
        }
      }
      if ((0, types_12.isAnyValueTypeName)(fullProtoTypeName)) {
        if (ctx.options.oneof === options_12.OneofOption.UNIONS) {
          chunks.push((0, ts_poet_1.code)`unwrap(message: ${(0, utils_12.wrapTypeName)(ctx.options, "Value")}): string | number | boolean | Object | null | Array<any> | undefined {
        if (message.kind?.$case === '${fieldNames.nullValue}') {
          return null;
        } else if (message.kind?.$case === '${fieldNames.numberValue}') {
          return message.kind?.${fieldNames.numberValue};
        } else if (message.kind?.$case === '${fieldNames.stringValue}') {
          return message.kind?.${fieldNames.stringValue};
        } else if (message.kind?.$case === '${fieldNames.boolValue}') {
          return message.kind?.${fieldNames.boolValue};
        } else if (message.kind?.$case === '${fieldNames.structValue}') {
          return message.kind?.${fieldNames.structValue};
        } else if (message.kind?.$case === '${fieldNames.listValue}') {
          return message.kind?.${fieldNames.listValue};
        } else {
          return undefined;
        }
      }`);
        } else if (ctx.options.oneof === options_12.OneofOption.UNIONS_VALUE) {
          chunks.push((0, ts_poet_1.code)`unwrap(message: ${(0, utils_12.wrapTypeName)(ctx.options, "Value")}): string | number | boolean | Object | null | Array<any> | undefined {
        return (message.kind?.$case === '${fieldNames.nullValue}') ? null : message.kind?.value;
      }`);
        } else {
          chunks.push((0, ts_poet_1.code)`unwrap(message: any): string | number | boolean | Object | null | Array<any> | undefined {
        if (message.${fieldNames.stringValue} !== undefined) {
          return message.${fieldNames.stringValue};
        } else if (message?.${fieldNames.numberValue} !== undefined) {
          return message.${fieldNames.numberValue};
        } else if (message?.${fieldNames.boolValue} !== undefined) {
          return message.${fieldNames.boolValue};
        } else if (message?.${fieldNames.structValue} !== undefined) {
          return message.${fieldNames.structValue} as any;
        } else if (message?.${fieldNames.listValue} !== undefined) {
          return message.${fieldNames.listValue};
        } else if (message?.${fieldNames.nullValue} !== undefined) {
          return null;
        }
        return undefined;
      }`);
        }
      }
      if ((0, types_12.isListValueTypeName)(fullProtoTypeName)) {
        chunks.push((0, ts_poet_1.code)`unwrap(message: ${ctx.options.useReadonlyTypes ? "any" : (0, utils_12.wrapTypeName)(ctx.options, "ListValue")}): Array<any> {
      if (message?.hasOwnProperty('values') && ${ctx.utils.globalThis}.Array.isArray(message.values)) {
        return message.values;
      } else {
        return message as any;
      }
    }`);
      }
      if ((0, types_12.isFieldMaskTypeName)(fullProtoTypeName)) {
        chunks.push(generateFieldMaskUnwrap(ctx));
      }
      return chunks;
    }
    function generateFieldMaskUnwrap(ctx) {
      const returnType = ctx.options.useOptionals === "all" ? "string[] | undefined" : "string[]";
      const pathModifier = ctx.options.useOptionals === "all" ? "?" : "";
      return (0, ts_poet_1.code)`unwrap(message: ${ctx.options.useReadonlyTypes ? "any" : (0, utils_12.wrapTypeName)(ctx.options, "FieldMask")}): ${returnType} {
    return message${pathModifier}.paths;
  }`;
    }
    function maybeReadonly(options) {
      return options.useReadonlyTypes ? "readonly " : "";
    }
    function maybeAsAny(options) {
      return options.useReadonlyTypes ? " as any" : "";
    }
  }
});

// hardware/generic/goldfish/emulator/ui/aquarium/node_modules/ts-proto/build/src/schema.js
var require_schema = __commonJS({
  "hardware/generic/goldfish/emulator/ui/aquarium/node_modules/ts-proto/build/src/schema.js"(exports2) {
    "use strict";
    Object.defineProperty(exports2, "__esModule", { value: true });
    exports2.generateSchema = generateSchema;
    var ts_proto_descriptors_12 = require_dist();
    var ts_poet_1 = require_build();
    var visit_1 = require_visit();
    var utils_12 = require_utils2();
    var types_12 = require_types();
    var wire_1 = require_wire();
    var options_12 = require_options();
    var fileDescriptorProto = (0, ts_poet_1.imp)("t:FileDescriptorProto@ts-proto-descriptors");
    var extensionCache = {};
    function generateSchema(ctx, fileDesc, sourceInfo) {
      const { options } = ctx;
      const chunks = [];
      fileDesc.extension.forEach((extension) => {
        if (!(extension.extendee in extensionCache)) {
          extensionCache[extension.extendee] = {};
        }
        extensionCache[extension.extendee][extension.number] = extension;
      });
      const outputSchemaOptions = ctx.options.outputSchema ? ctx.options.outputSchema : [];
      const outputFileDescriptor = !outputSchemaOptions.includes(options_12.OutputSchemaOption.NO_FILE_DESCRIPTOR);
      const outputAsConst = outputSchemaOptions.includes(options_12.OutputSchemaOption.CONST);
      chunks.push((0, ts_poet_1.code)`
    type ProtoMetaMessageOptions = {
      options?: { [key: string]: any };
      fields?: { [key: string]: { [key: string]: any } };
      oneof?: { [key: string]: { [key: string]: any } };
      nested?: { [key: string]: ProtoMetaMessageOptions };
    };

    export interface ProtoMetadata {
      ${outputFileDescriptor ? (0, ts_poet_1.code)`fileDescriptor: ${fileDescriptorProto};\n` : ""}references: { [key: string]: any };
      dependencies?: ProtoMetadata[];
      options?: {
        options?: { [key: string]: any };
        services?: {
          [key: string]: {
            options?: { [key: string]: any };
            methods?: { [key: string]: { [key: string]: any } };
          }
        };
        messages?: {
          [key: string]: ProtoMetaMessageOptions;
        };
        enums?: {
          [key: string]: {
            options?: { [key: string]: any };
            values?: { [key: string]: { [key: string]: any } };
          };
        };
      };
    }
  `);
      const references = [];
      function addReference(localName, symbol) {
        references.push((0, ts_poet_1.code)`'.${(0, utils_12.maybePrefixPackage)(fileDesc, localName.replace(/_/g, "."))}': ${symbol}`);
      }
      (0, visit_1.visit)(fileDesc, sourceInfo, (fullName) => {
        if (options.outputEncodeMethods) {
          addReference(fullName, fullName);
        }
      }, options, (fullName) => {
        addReference(fullName, fullName);
      });
      (0, visit_1.visitServices)(fileDesc, sourceInfo, (serviceDesc) => {
        if (options.outputClientImpl) {
          const suffix = options.outputServices.includes(options_12.ServiceOption.GRPC) ? "Client" : "ClientImpl";
          addReference(serviceDesc.name, `${serviceDesc.name}${suffix}`);
        }
      });
      const dependencies = fileDesc.dependency.map((dep) => {
        return (0, ts_poet_1.code)`${(0, utils_12.impFile)(options, `protoMetadata@./${dep.replace(".proto", "")}${options.fileSuffix}`)}`;
      });
      const descriptor = ts_proto_descriptors_12.FileDescriptorProto.fromPartial(fileDesc);
      descriptor.sourceCodeInfo = {
        location: descriptor.sourceCodeInfo?.location.filter((loc) => loc["leadingComments"] || loc["trailingComments"]) || []
      };
      let fileOptions;
      if (fileDesc.options) {
        fileOptions = encodedOptionsToOptions(ctx, ".google.protobuf.FileOptions", fileDesc.options._unknownFields);
        delete fileDesc.options._unknownFields;
      }
      const messagesOptions = [];
      (fileDesc.messageType || []).forEach((message) => {
        const resolvedMessage = resolveMessageOptions(ctx, message);
        if (resolvedMessage) {
          messagesOptions.push(resolvedMessage);
        }
      });
      const servicesOptions = [];
      (fileDesc.service || []).forEach((service) => {
        const methodsOptions = [];
        service.method.forEach((method) => {
          if (method.options) {
            const methodOptions = encodedOptionsToOptions(ctx, ".google.protobuf.MethodOptions", method.options._unknownFields);
            delete method.options._unknownFields;
            if (methodOptions) {
              methodsOptions.push((0, ts_poet_1.code)`'${method.name}': ${methodOptions}`);
            }
          }
        });
        let serviceOptions;
        if (service.options) {
          serviceOptions = encodedOptionsToOptions(ctx, ".google.protobuf.ServiceOptions", service.options._unknownFields);
          delete service.options._unknownFields;
        }
        if (methodsOptions.length > 0 || serviceOptions) {
          servicesOptions.push((0, ts_poet_1.code)`
        '${service.name}': {
          ${serviceOptions ? (0, ts_poet_1.code)`options: ${serviceOptions},` : ""}
          methods: {${(0, ts_poet_1.joinCode)(methodsOptions, { on: "," })}}
        }
      `);
        }
      });
      const enumsOptions = [];
      (fileDesc.enumType || []).forEach((Enum) => {
        const valuesOptions = [];
        Enum.value.forEach((value) => {
          if (value.options) {
            const valueOptions = encodedOptionsToOptions(ctx, ".google.protobuf.EnumValueOptions", value.options._unknownFields);
            delete value.options._unknownFields;
            if (valueOptions) {
              valuesOptions.push((0, ts_poet_1.code)`'${value.name}': ${valueOptions}`);
            }
          }
        });
        let enumOptions;
        if (Enum.options) {
          enumOptions = encodedOptionsToOptions(ctx, ".google.protobuf.EnumOptions", Enum.options._unknownFields);
          delete Enum.options._unknownFields;
        }
        if (valuesOptions.length > 0 || enumOptions) {
          enumsOptions.push((0, ts_poet_1.code)`
        '${Enum.name}': {
          ${enumOptions ? (0, ts_poet_1.code)`options: ${enumOptions},` : ""}
          values: {${(0, ts_poet_1.joinCode)(valuesOptions, { on: "," })}}
        }
      `);
        }
      });
      chunks.push((0, ts_poet_1.code)`
    export const ${(0, ts_poet_1.def)("protoMetadata")}${outputAsConst ? "" : ": ProtoMetadata"} = {
      ${outputFileDescriptor ? (0, ts_poet_1.code)`fileDescriptor: ${descriptor},\n` : ""}references: { ${(0, ts_poet_1.joinCode)(references, {
        on: ","
      })} },
      dependencies: [${(0, ts_poet_1.joinCode)(dependencies, { on: "," })}],
      ${fileOptions || messagesOptions.length > 0 || servicesOptions.length > 0 || enumsOptions.length > 0 ? (0, ts_poet_1.code)`options: {
          ${fileOptions ? (0, ts_poet_1.code)`options: ${fileOptions},` : ""}
          ${messagesOptions.length > 0 ? (0, ts_poet_1.code)`messages: {${(0, ts_poet_1.joinCode)(messagesOptions, { on: "," })}},` : ""}
          ${servicesOptions.length > 0 ? (0, ts_poet_1.code)`services: {${(0, ts_poet_1.joinCode)(servicesOptions, { on: "," })}},` : ""}
          ${enumsOptions.length > 0 ? (0, ts_poet_1.code)`enums: {${(0, ts_poet_1.joinCode)(enumsOptions, { on: "," })}}` : ""}
        }` : ""}
    }${outputAsConst ? " as const satisfies ProtoMetadata" : ""}
  `);
      return chunks;
    }
    function getExtensionValue(ctx, extension, data) {
      if (extension.type == ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_MESSAGE) {
        const typeName = (0, types_12.basicTypeName)(ctx, extension);
        const resultBuffer = Buffer.concat(data.map((d) => {
          const bytes = new wire_1.BinaryReader(d).bytes();
          return Buffer.from(bytes);
        }));
        const result = resultBuffer.toString("base64");
        const encoded = ctx.options.env === options_12.EnvOption.NODE ? (0, ts_poet_1.code)`Buffer.from('${result}', 'base64')` : (0, ts_poet_1.code)`${ctx.utils.bytesFromBase64}("${result}")`;
        return (0, ts_poet_1.code)`'${extension.name}': ${typeName}.decode(${encoded})`;
      } else {
        const reader = new wire_1.BinaryReader(data[0]);
        let value = reader[(0, types_12.toReaderCall)(extension)]();
        if (typeof value === "string") {
          value = JSON.stringify(value);
        }
        return (0, ts_poet_1.code)`'${extension.name}': ${value}`;
      }
    }
    function encodedOptionsToOptions(ctx, extendee, encodedOptions) {
      if (!encodedOptions) {
        return void 0;
      }
      const resultOptions = [];
      for (const key in encodedOptions) {
        const value = encodedOptions[key];
        const extension = extensionCache[extendee]?.[parseInt(key, 10) >>> 3];
        if (extension && shouldAddOptionDefinition(ctx, extension)) {
          resultOptions.push(getExtensionValue(ctx, extension, value));
        }
      }
      if (resultOptions.length == 0) {
        return void 0;
      }
      return (0, ts_poet_1.code)`{${(0, ts_poet_1.joinCode)(resultOptions, { on: "," })}}`;
    }
    function shouldAddOptionDefinition(ctx, extension) {
      return extension.type !== ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_MESSAGE || ctx.options.outputEncodeMethods === true || ctx.options.outputEncodeMethods == "decode-only";
    }
    function resolveMessageOptions(ctx, message) {
      const fieldsOptions = [];
      message.field.forEach((field) => {
        if (field.options) {
          const fieldOptions = encodedOptionsToOptions(ctx, ".google.protobuf.FieldOptions", field.options._unknownFields);
          delete field.options._unknownFields;
          if (fieldOptions) {
            fieldsOptions.push((0, ts_poet_1.code)`'${field.name}': ${fieldOptions}`);
          }
        }
      });
      const oneOfsOptions = [];
      message.oneofDecl.forEach((oneOf) => {
        if (oneOf.options) {
          const oneOfOptions = encodedOptionsToOptions(ctx, ".google.protobuf.OneofOptions", oneOf.options._unknownFields);
          delete oneOf.options._unknownFields;
          if (oneOfOptions) {
            oneOfsOptions.push((0, ts_poet_1.code)`'${oneOf.name}': ${oneOfOptions}`);
          }
        }
      });
      let nestedOptions = [];
      if (message.nestedType && message.nestedType.length > 0) {
        message.nestedType.forEach((nested) => {
          const resolvedMessage = resolveMessageOptions(ctx, nested);
          if (resolvedMessage) {
            nestedOptions.push(resolvedMessage);
          }
        });
      }
      let messageOptions;
      if (message.options) {
        messageOptions = encodedOptionsToOptions(ctx, ".google.protobuf.MessageOptions", message.options._unknownFields);
        delete message.options._unknownFields;
      }
      if (fieldsOptions.length > 0 || oneOfsOptions.length > 0 || nestedOptions.length > 0 || messageOptions) {
        return (0, ts_poet_1.code)`
      '${message.name}': {
        ${messageOptions ? (0, ts_poet_1.code)`options: ${messageOptions},` : ""}
        ${fieldsOptions.length > 0 ? (0, ts_poet_1.code)`fields: {${(0, ts_poet_1.joinCode)(fieldsOptions, { on: "," })}},` : ""}
        ${oneOfsOptions.length > 0 ? (0, ts_poet_1.code)`oneof: {${(0, ts_poet_1.joinCode)(oneOfsOptions, { on: "," })}},` : ""}
        ${nestedOptions.length > 0 ? (0, ts_poet_1.code)`nested: {${(0, ts_poet_1.joinCode)(nestedOptions, { on: "," })}},` : ""}
      }
    `;
      }
    }
  }
});

// hardware/generic/goldfish/emulator/ui/aquarium/node_modules/ts-proto/build/src/main.js
var require_main = __commonJS({
  "hardware/generic/goldfish/emulator/ui/aquarium/node_modules/ts-proto/build/src/main.js"(exports2) {
    "use strict";
    Object.defineProperty(exports2, "__esModule", { value: true });
    exports2.contextTypeVar = void 0;
    exports2.generateFile = generateFile;
    exports2.makeUtils = makeUtils;
    var ts_poet_1 = require_build();
    var ConditionalOutput_1 = require_ConditionalOutput();
    var ts_proto_descriptors_12 = require_dist();
    var case_1 = require_case();
    var enums_1 = require_enums();
    var generate_async_iterable_1 = require_generate_async_iterable();
    var generate_generic_service_definition_1 = require_generate_generic_service_definition();
    var generate_grpc_js_1 = require_generate_grpc_js();
    var generate_grpc_web_1 = require_generate_grpc_web();
    var generate_nestjs_1 = require_generate_nestjs();
    var generate_nice_grpc_1 = require_generate_nice_grpc();
    var generate_services_1 = require_generate_services();
    var generate_struct_wrappers_1 = require_generate_struct_wrappers();
    var options_12 = require_options();
    var schema_1 = require_schema();
    var sourceInfo_1 = require_sourceInfo();
    var types_12 = require_types();
    var utils_12 = require_utils2();
    var visit_1 = require_visit();
    function generateFile(ctx, fileDesc) {
      const { options, utils } = ctx;
      if (options.useOptionals === false) {
        console.warn("ts-proto: Passing useOptionals as a boolean option is deprecated and will be removed in a future version. Please pass the string 'none' instead of false.");
        options.useOptionals = "none";
      } else if (options.useOptionals === true) {
        console.warn("ts-proto: Passing useOptionals as a boolean option is deprecated and will be removed in a future version. Please pass the string 'messages' instead of true.");
        options.useOptionals = "messages";
      }
      const suffix = `${options.fileSuffix}.ts`;
      const moduleName = fileDesc.name.replace(".proto", suffix);
      const chunks = [];
      if (options.exportCommonSymbols) {
        chunks.push((0, ts_poet_1.code)`export const protobufPackage = '${fileDesc.package}';`);
      }
      const sourceInfo = sourceInfo_1.default.fromDescriptor(fileDesc);
      const headerComment = sourceInfo.lookup(sourceInfo_1.Fields.file.syntax, void 0);
      (0, utils_12.maybeAddComment)(options, headerComment, chunks, fileDesc.options?.deprecated);
      for (let svc of fileDesc.service) {
        for (let i = 0; i < svc.method.length; i++) {
          svc.method[i] = new utils_12.FormattedMethodDescriptor(svc.method[i], options);
        }
      }
      (0, visit_1.visit)(fileDesc, sourceInfo, (fullName, message, sInfo, fullProtoTypeName) => {
        chunks.push(generateInterfaceDeclaration(ctx, fullName, message, sInfo, (0, utils_12.maybePrefixPackage)(fileDesc, fullProtoTypeName)));
      }, options, (fullName, enumDesc, sInfo) => {
        chunks.push((0, enums_1.generateEnum)(ctx, fullName, enumDesc, sInfo));
      });
      if (options.nestJs) {
        if (options.exportCommonSymbols) {
          const prefix = (0, case_1.camelToSnake)(fileDesc.package.replace(/\./g, "_"));
          chunks.push((0, ts_poet_1.code)`export const ${prefix}_PACKAGE_NAME = '${fileDesc.package}';`);
        }
        if (options.useDate === options_12.DateOption.DATE && fileDesc.messageType.find(hasTimestampField)) {
          chunks.push(makeProtobufTimestampWrapper());
        }
      }
      if (options.outputEncodeMethods || options.outputJsonMethods || options.outputTypeAnnotations || options.outputTypeRegistry || options.nestJs) {
        (0, visit_1.visit)(fileDesc, sourceInfo, (fullName, message, _sInfo, fullProtoTypeName) => {
          const fullTypeName = (0, utils_12.maybePrefixPackage)(fileDesc, fullProtoTypeName);
          const outputWrapAndUnwrap = (0, generate_struct_wrappers_1.isWrapperType)(fullTypeName);
          if (options.outputEncodeMethods && options.outputEncodeMethods !== "encode-no-creation" && options.outputEncodeMethods !== "encode-only" && (options.outputDecodeIncludeTypes === "" || new RegExp(options.outputDecodeIncludeTypes).test(fullTypeName)) || options.outputPartialMethods || outputWrapAndUnwrap) {
            chunks.push(generateBaseInstanceFactory(ctx, fullName, message, fullTypeName));
          }
          const staticMembers = [];
          const hasTypeMember = options.outputTypeAnnotations || options.outputTypeRegistry;
          if (hasTypeMember) {
            staticMembers.push((0, ts_poet_1.code)`$type: '${fullTypeName}' as const`);
          }
          if (options.outputExtensions) {
            for (const extension of message.extension) {
              const { name, type, extensionInfo } = generateExtension(ctx, message, extension);
              staticMembers.push((0, ts_poet_1.code)`${name}: <${ctx.utils.Extension}<${type}>> ${extensionInfo}`);
            }
          }
          if (options.outputEncodeMethods) {
            if (options.outputEncodeMethods === true || options.outputEncodeMethods === "encode-only" || options.outputEncodeMethods === "encode-no-creation") {
              if (options.outputEncodeIncludeTypes === "" || new RegExp(options.outputEncodeIncludeTypes).test(fullTypeName)) {
                staticMembers.push(generateEncode(ctx, fullName, message));
                if (options.outputExtensions && options.unknownFields && message.extensionRange.length) {
                  staticMembers.push(generateSetExtension(ctx, fullName));
                }
              } else if (options.outputEncodeMethods === true) {
                staticMembers.push(generateEmptyEncode(fullName));
              }
            }
            if (options.outputEncodeMethods === true || options.outputEncodeMethods === "decode-only") {
              if (options.outputDecodeIncludeTypes === "" || new RegExp(options.outputDecodeIncludeTypes).test(fullTypeName)) {
                staticMembers.push(generateDecode(ctx, fullName, message));
                if (options.outputExtensions && options.unknownFields && message.extensionRange.length) {
                  staticMembers.push(generateGetExtension(ctx, fullName));
                }
              } else if (options.outputEncodeMethods === true) {
                staticMembers.push(generateEmptyDecode(fullName));
              }
            }
          }
          if (options.useAsyncIterable) {
            staticMembers.push((0, generate_async_iterable_1.generateEncodeTransform)(ctx.utils, fullName));
            staticMembers.push((0, generate_async_iterable_1.generateDecodeTransform)(ctx.utils, fullName));
          }
          if (options.outputJsonMethods) {
            if (options.outputJsonMethods === true || options.outputJsonMethods === "from-only") {
              staticMembers.push(generateFromJson(ctx, fullName, fullTypeName, message));
            }
            if (options.outputJsonMethods === true || options.outputJsonMethods === "to-only") {
              staticMembers.push(generateToJson(ctx, fullName, fullTypeName, message));
            }
          }
          if (options.outputPartialMethods) {
            staticMembers.push(generateFromPartial(ctx, fullName, message));
          }
          const structFieldNames = {
            nullValue: (0, case_1.maybeSnakeToCamel)("null_value", ctx.options),
            numberValue: (0, case_1.maybeSnakeToCamel)("number_value", ctx.options),
            stringValue: (0, case_1.maybeSnakeToCamel)("string_value", ctx.options),
            boolValue: (0, case_1.maybeSnakeToCamel)("bool_value", ctx.options),
            structValue: (0, case_1.maybeSnakeToCamel)("struct_value", ctx.options),
            listValue: (0, case_1.maybeSnakeToCamel)("list_value", ctx.options)
          };
          if (options.nestJs) {
            staticMembers.push(...(0, generate_struct_wrappers_1.generateWrapDeep)(ctx, fullTypeName, structFieldNames));
            staticMembers.push(...(0, generate_struct_wrappers_1.generateUnwrapDeep)(ctx, fullTypeName, structFieldNames));
          } else {
            staticMembers.push(...(0, generate_struct_wrappers_1.generateWrapShallow)(ctx, fullTypeName, structFieldNames));
            staticMembers.push(...(0, generate_struct_wrappers_1.generateUnwrapShallow)(ctx, fullTypeName, structFieldNames));
          }
          if (staticMembers.length > 0) {
            const messageFnsTypeParameters = [fullName, hasTypeMember && `'${fullTypeName}'`].filter((p) => !!p).join(", ");
            const interfaces = [(0, ts_poet_1.code)`${utils.MessageFns}<${messageFnsTypeParameters}>`];
            if (options.outputEncodeMethods && options.outputExtensions && options.unknownFields && message.extensionRange.length) {
              interfaces.push((0, ts_poet_1.code)`${utils.ExtensionFns}<${fullName}>`);
            }
            if ((0, types_12.isStructTypeName)(fullTypeName)) {
              interfaces.push((0, ts_poet_1.code)`${utils.StructWrapperFns}`);
            } else if ((0, types_12.isAnyValueTypeName)(fullTypeName)) {
              interfaces.push((0, ts_poet_1.code)`${utils.AnyValueWrapperFns}`);
            } else if ((0, types_12.isListValueTypeName)(fullTypeName)) {
              interfaces.push((0, ts_poet_1.code)`${utils.ListValueWrapperFns}`);
            } else if ((0, types_12.isFieldMaskTypeName)(fullTypeName)) {
              interfaces.push((0, ts_poet_1.code)`${utils.FieldMaskWrapperFns}`);
            }
            if (options.outputExtensions) {
              for (const extension of message.extension) {
                const name = (0, case_1.maybeSnakeToCamel)(extension.name, ctx.options);
                const type = (0, types_12.toTypeName)(ctx, message, extension);
                interfaces.push((0, ts_poet_1.code)`${utils.ExtensionHolder}<"${name}", ${type}>`);
              }
            }
            chunks.push((0, ts_poet_1.code)`
            export const ${(0, ts_poet_1.def)(fullName)}: ${(0, ts_poet_1.joinCode)(interfaces, { on: " & " })} = {
              ${(0, ts_poet_1.joinCode)(staticMembers, { on: ",\n\n" })}
            };
          `);
          }
          if (options.outputTypeRegistry) {
            const messageTypeRegistry = (0, utils_12.impFile)(options, "messageTypeRegistry@./typeRegistry");
            chunks.push((0, ts_poet_1.code)`
            ${messageTypeRegistry}.set(${fullName}.$type, ${fullName});
          `);
          }
        }, options);
      }
      if (options.outputExtensions) {
        for (const extension of fileDesc.extension) {
          const { name, type, extensionInfo } = generateExtension(ctx, void 0, extension);
          chunks.push((0, ts_poet_1.code)`export const ${name}: ${ctx.utils.Extension}<${type}> = ${extensionInfo};`);
        }
      }
      if (options.nestJs) {
        if (fileDesc.messageType.find(hasStructTypeField)) {
          chunks.push(makeProtobufStructWrapper(options));
        }
      }
      let hasServerStreamingMethods = false;
      let hasStreamingMethods = false;
      (0, visit_1.visitServices)(fileDesc, sourceInfo, (serviceDesc, sInfo) => {
        if (options.nestJs) {
          chunks.push((0, generate_nestjs_1.generateNestjsServiceClient)(ctx, fileDesc, sInfo, serviceDesc));
          chunks.push((0, generate_nestjs_1.generateNestjsServiceController)(ctx, fileDesc, sInfo, serviceDesc));
          chunks.push((0, generate_nestjs_1.generateNestjsGrpcServiceMethodsDecorator)(ctx, serviceDesc));
          let serviceConstName = `${(0, case_1.camelToSnake)(serviceDesc.name)}_NAME`;
          if (!serviceDesc.name.toLowerCase().endsWith("service")) {
            serviceConstName = `${(0, case_1.camelToSnake)(serviceDesc.name)}_SERVICE_NAME`;
          }
          chunks.push((0, ts_poet_1.code)`export const ${serviceConstName} = "${serviceDesc.name}";`);
        }
        const uniqueServices = [...new Set(options.outputServices)].sort();
        uniqueServices.forEach((outputService) => {
          if (outputService === options_12.ServiceOption.GRPC) {
            chunks.push((0, generate_grpc_js_1.generateGrpcJsService)(ctx, fileDesc, sInfo, serviceDesc));
          } else if (outputService === options_12.ServiceOption.NICE_GRPC) {
            chunks.push((0, generate_nice_grpc_1.generateNiceGrpcService)(ctx, fileDesc, sInfo, serviceDesc));
          } else if (outputService === options_12.ServiceOption.GENERIC) {
            chunks.push((0, generate_generic_service_definition_1.generateGenericServiceDefinition)(ctx, fileDesc, sInfo, serviceDesc));
          } else if (outputService === options_12.ServiceOption.DEFAULT) {
            chunks.push((0, generate_services_1.generateService)(ctx, fileDesc, sInfo, serviceDesc));
            if (options.outputClientImpl === true) {
              chunks.push((0, generate_services_1.generateServiceClientImpl)(ctx, fileDesc, serviceDesc));
            } else if (options.outputClientImpl === "grpc-web") {
              chunks.push((0, generate_grpc_web_1.generateGrpcClientImpl)(ctx, fileDesc, serviceDesc));
              chunks.push((0, generate_grpc_web_1.generateGrpcServiceDesc)(fileDesc, serviceDesc));
              serviceDesc.method.forEach((method) => {
                if (!method.clientStreaming) {
                  chunks.push((0, generate_grpc_web_1.generateGrpcMethodDesc)(ctx, serviceDesc, method));
                }
                if (method.serverStreaming) {
                  hasServerStreamingMethods = true;
                }
              });
            }
          }
        });
        serviceDesc.method.forEach((methodDesc, _index) => {
          if (methodDesc.serverStreaming || methodDesc.clientStreaming) {
            hasStreamingMethods = true;
          }
        });
      });
      if (options.outputServices.includes(options_12.ServiceOption.DEFAULT) && options.outputClientImpl && fileDesc.service.length > 0) {
        if (options.outputClientImpl === true) {
          chunks.push((0, generate_services_1.generateRpcType)(ctx, hasStreamingMethods));
        } else if (options.outputClientImpl === "grpc-web") {
          chunks.push((0, generate_grpc_web_1.addGrpcWebMisc)(ctx, hasServerStreamingMethods));
        }
      }
      if (options.context) {
        chunks.push((0, generate_services_1.generateDataLoaderOptionsType)());
        chunks.push((0, generate_services_1.generateDataLoadersType)());
      }
      if (options.outputSchema) {
        chunks.push(...(0, schema_1.generateSchema)(ctx, fileDesc, sourceInfo));
      }
      if (options.esModuleInterop && chunks.length === 0) {
        chunks.push((0, ts_poet_1.code)`export {};`);
      }
      chunks.push(...Object.values(utils).map((v) => {
        if (v instanceof ConditionalOutput_1.ConditionalOutput) {
          return (0, ts_poet_1.code)`${v.ifUsed}`;
        } else {
          return (0, ts_poet_1.code)``;
        }
      }));
      for (let svc of fileDesc.service) {
        for (let i = 0; i < svc.method.length; i++) {
          const methodInfo = svc.method[i];
          (0, utils_12.assertInstanceOf)(methodInfo, utils_12.FormattedMethodDescriptor);
          svc.method[i] = methodInfo.getSource();
        }
      }
      return [moduleName, (0, ts_poet_1.joinCode)(chunks, { on: "\n\n" })];
    }
    function makeUtils(options) {
      const bytes = makeByteUtils(options);
      const longs = makeLongUtils(options, bytes);
      const deepPartial = makeDeepPartial(options, longs);
      const extension = makeExtensionClass(options);
      return {
        ...bytes,
        ...deepPartial,
        ...makeObjectIdMethods(),
        ...makeTimestampMethods(options, longs, bytes),
        ...longs,
        ...makeComparisonUtils(),
        ...makeNiceGrpcServerStreamingMethodResult(options),
        ...makeGrpcWebErrorClass(bytes),
        ...extension,
        ...makeAssertionUtils(bytes),
        ...makeMessageFns(options, deepPartial, extension)
      };
    }
    function makeProtobufTimestampWrapper() {
      const wrappers = (0, ts_poet_1.imp)("wrappers@protobufjs");
      return (0, ts_poet_1.code)`
      ${wrappers}['.google.protobuf.Timestamp'] = {
        fromObject(value: Date) {
          return {
            seconds: value.getTime() / 1000,
            nanos: (value.getTime() % 1000) * 1e6,
          };
        },
        toObject(message: { seconds: number; nanos: number }) {
          return new Date(message.seconds * 1000 + message.nanos / 1e6);
        },
      } as any;`;
    }
    function hasField(message, predicate) {
      return message.field.some(predicate) || message.nestedType.some((nestedMessage) => hasField(nestedMessage, predicate));
    }
    function hasTimestampField(message) {
      return hasField(message, types_12.isTimestamp);
    }
    function hasStructTypeField(message) {
      return hasField(message, types_12.isStructType);
    }
    function makeProtobufStructWrapper(options) {
      const wrappers = (0, ts_poet_1.imp)("wrappers@protobufjs");
      const Struct = (0, utils_12.impProto)(options, "google/protobuf/struct", (0, utils_12.wrapTypeName)(options, "Struct"));
      return (0, ts_poet_1.code)`
    ${wrappers}['.google.protobuf.Struct'] = {
      fromObject: ${Struct}.wrap,
      toObject: ${Struct}.unwrap,
    } as any;`;
    }
    function makeLongUtils(options, bytes) {
      const Long = (0, ts_poet_1.imp)("Long=long");
      const numberToLong = (0, ts_poet_1.conditionalOutput)("numberToLong", (0, ts_poet_1.code)`
      function numberToLong(number: number) {
        return ${Long}.fromNumber(number);
      }
    `);
      const longToNumber = (0, ts_poet_1.conditionalOutput)("longToNumber", (0, ts_poet_1.code)`
      function longToNumber(int64: { toString(): string }): number {
        const num = ${bytes.globalThis}.Number(int64.toString());
        if (num > ${bytes.globalThis}.Number.MAX_SAFE_INTEGER) {
          throw new ${bytes.globalThis}.Error("Value is larger than Number.MAX_SAFE_INTEGER")
        }
        if (num < ${bytes.globalThis}.Number.MIN_SAFE_INTEGER) {
          throw new ${bytes.globalThis}.Error("Value is smaller than Number.MIN_SAFE_INTEGER")
        }
        return num;
      }
    `);
      return { numberToLong, longToNumber, Long };
    }
    function makeByteUtils(options) {
      const globalThisPolyfill = (0, ts_poet_1.conditionalOutput)("gt", (0, ts_poet_1.code)`
      declare const self: any | undefined;
      declare const window: any | undefined;
      declare const global: any | undefined;
      const gt: any = (() => {
        if (typeof globalThis !== "undefined") return globalThis;
        if (typeof self !== "undefined") return self;
        if (typeof window !== "undefined") return window;
        if (typeof global !== "undefined") return global;
        throw "Unable to locate global object";
      })();
    `);
      const globalThis2 = options.globalThisPolyfill ? globalThisPolyfill : (0, ts_poet_1.conditionalOutput)("globalThis", (0, ts_poet_1.code)``);
      function getBytesFromBase64Snippet() {
        const bytesFromBase64NodeSnippet = (0, ts_poet_1.code)`
      return Uint8Array.from(${globalThis2}.Buffer.from(b64, 'base64'));
    `;
        const bytesFromBase64BrowserSnippet = (0, ts_poet_1.code)`
      const bin = ${globalThis2}.atob(b64);
      const arr = new Uint8Array(bin.length);
      for (let i = 0; i < bin.length; ++i) {
          arr[i] = bin.charCodeAt(i);
      }
      return arr;
    `;
        switch (options.env) {
          case options_12.EnvOption.NODE:
            return bytesFromBase64NodeSnippet;
          case options_12.EnvOption.BROWSER:
            return bytesFromBase64BrowserSnippet;
          default:
            return (0, ts_poet_1.code)`
        if ((${globalThis2} as any).Buffer) {
          return Uint8Array.from((${globalThis2} as any).Buffer.from(b64, 'base64'));
          } else {
            ${bytesFromBase64BrowserSnippet}
          }
        `;
        }
      }
      const bytesFromBase64 = (0, ts_poet_1.conditionalOutput)("bytesFromBase64", (0, ts_poet_1.code)`
      function bytesFromBase64(b64: string): Uint8Array {
        ${getBytesFromBase64Snippet()}
      }
    `);
      function getBase64FromBytesSnippet() {
        const base64FromBytesNodeSnippet = (0, ts_poet_1.code)`
      return ${globalThis2}.Buffer.from(arr).toString('base64');
    `;
        const base64FromBytesBrowserSnippet = (0, ts_poet_1.code)`
      const bin: string[] = [];
      arr.forEach((byte) => {
        bin.push(${globalThis2}.String.fromCharCode(byte));
      });
      return ${globalThis2}.btoa(bin.join(''));
    `;
        switch (options.env) {
          case options_12.EnvOption.NODE:
            return base64FromBytesNodeSnippet;
          case options_12.EnvOption.BROWSER:
            return base64FromBytesBrowserSnippet;
          default:
            return (0, ts_poet_1.code)`
          if ((${globalThis2} as any).Buffer) {
            return (${globalThis2} as any).Buffer.from(arr).toString('base64');
          } else {
            ${base64FromBytesBrowserSnippet}
          }
        `;
        }
      }
      const base64FromBytes = (0, ts_poet_1.conditionalOutput)("base64FromBytes", (0, ts_poet_1.code)`
      function base64FromBytes(arr: Uint8Array): string {
        ${getBase64FromBytesSnippet()}
      }
    `);
      return { globalThis: globalThis2, bytesFromBase64, base64FromBytes };
    }
    function makeDeepPartial(options, longs) {
      let oneofCase = "";
      if (options.oneof === options_12.OneofOption.UNIONS) {
        oneofCase = `
      : T extends { ${maybeReadonly(options)}$case: string }
      ? { [K in keyof Omit<T, '$case'>]?: DeepPartial<T[K]> } & { ${maybeReadonly(options)}$case: T['$case'] }
    `;
      } else if (options.oneof === options_12.OneofOption.UNIONS_VALUE) {
        oneofCase = `
      : T extends { ${maybeReadonly(options)}$case: string; value: unknown; }
      ? { ${maybeReadonly(options)}$case: T['$case']; value?: DeepPartial<T['value']>; }
    `;
      }
      const maybeExport = options.exportCommonSymbols ? "export" : "";
      const maybeLong = options.forceLong === options_12.LongOption.LONG ? (0, ts_poet_1.code)` : T extends ${longs.Long} ? string | number | Long ` : "";
      const maybeBigInt = options.forceLong === options_12.LongOption.BIGINT ? (0, ts_poet_1.code)`T extends bigint ? string | number | bigint : ` : "";
      const optionalBuiltins = [];
      if (options.forceLong === options_12.LongOption.BIGINT) {
        optionalBuiltins.push("bigint");
      }
      if (options.useDate === options_12.DateOption.TEMPORAL) {
        optionalBuiltins.push("Temporal.Instant");
      }
      const Builtin = (0, ts_poet_1.conditionalOutput)("Builtin", (0, ts_poet_1.code)`type Builtin = Date | Function | Uint8Array | string | number | boolean ${optionalBuiltins.length ? `| ${optionalBuiltins.join(" | ")} ` : ""}| undefined;`);
      const maybeExcludeType = (0, options_12.addTypeToMessages)(options) ? `| '$type'` : "";
      const Exact = (0, ts_poet_1.conditionalOutput)("Exact", (0, ts_poet_1.code)`
      type KeysOfUnion<T> = T extends T ? keyof T : never;
      ${maybeExport} type Exact<P, I extends P> = P extends ${Builtin}
        ? P
        : P &
        { [K in keyof P]: Exact<P[K], I[K]> } & { [K in Exclude<keyof I, KeysOfUnion<P> ${maybeExcludeType}>]: never };
    `);
      const keys = (0, options_12.addTypeToMessages)(options) ? (0, ts_poet_1.code)`Exclude<keyof T, '$type'>` : (0, ts_poet_1.code)`keyof T`;
      const DeepPartial = (0, ts_poet_1.conditionalOutput)("DeepPartial", (0, ts_poet_1.code)`
      ${maybeExport} type DeepPartial<T> = ${maybeBigInt} T extends ${Builtin}
        ? T
        ${maybeLong}
        : T extends globalThis.Array<infer U>
        ? globalThis.Array<DeepPartial<U>>
        : T extends ReadonlyArray<infer U>
        ? ReadonlyArray<DeepPartial<U>>${oneofCase}
        : T extends {}
        ? { [K in ${keys}]?: DeepPartial<T[K]> }
        : Partial<T>;
    `);
      return { Builtin, DeepPartial, Exact };
    }
    function makeMessageFns(options, deepPartial, extension) {
      const BinaryWriter = (0, ts_poet_1.imp)("t:BinaryWriter@@bufbuild/protobuf/wire");
      const BinaryReader = (0, ts_poet_1.imp)("t:BinaryReader@@bufbuild/protobuf/wire");
      const { Exact, DeepPartial } = deepPartial;
      const { Extension } = extension;
      const commonStaticMembers = [];
      const extensionStaticMembers = [];
      const hasTypeMember = options.outputTypeAnnotations || options.outputTypeRegistry;
      if (hasTypeMember) {
        commonStaticMembers.push((0, ts_poet_1.code)`readonly $type: V;`);
      }
      if (options.outputEncodeMethods) {
        if (options.outputEncodeMethods === true || options.outputEncodeMethods === "encode-only" || options.outputEncodeMethods === "encode-no-creation") {
          commonStaticMembers.push((0, ts_poet_1.code)`
        encode(message: T, writer?: ${BinaryWriter}): ${BinaryWriter};
      `);
          if (options.outputExtensions && options.unknownFields) {
            extensionStaticMembers.push((0, ts_poet_1.code)`
          setExtension<E>(message: T, extension: ${Extension}<E>, value: E): void;
        `);
          }
        }
        if (options.outputEncodeMethods === true || options.outputEncodeMethods === "decode-only") {
          commonStaticMembers.push((0, ts_poet_1.code)`
        decode(input: ${BinaryReader} | Uint8Array, length?: number): T;
      `);
          if (options.outputExtensions && options.unknownFields) {
            extensionStaticMembers.push((0, ts_poet_1.code)`
          getExtension<E>(message: T, extension: ${Extension}<E>): E | undefined;
        `);
          }
        }
      }
      if (options.useAsyncIterable) {
        commonStaticMembers.push((0, ts_poet_1.code)`
      encodeTransform(
        source: AsyncIterable<T | T[]> | Iterable<T | T[]>
      ): AsyncIterable<Uint8Array>;
    `);
        commonStaticMembers.push((0, ts_poet_1.code)`
      decodeTransform(
        source: AsyncIterable<Uint8Array | Uint8Array[]> | Iterable<Uint8Array | Uint8Array[]>
      ): AsyncIterable<T>;
    `);
      }
      if (options.outputJsonMethods) {
        if (options.outputJsonMethods === true || options.outputJsonMethods === "from-only") {
          commonStaticMembers.push((0, ts_poet_1.code)`fromJSON(object: any): T;`);
        }
        if (options.outputJsonMethods === true || options.outputJsonMethods === "to-only") {
          commonStaticMembers.push((0, ts_poet_1.code)`toJSON(message: T): unknown;`);
        }
      }
      if (options.outputPartialMethods) {
        if (options.useExactTypes) {
          commonStaticMembers.push((0, ts_poet_1.code)`create<I extends ${Exact}<${DeepPartial}<T>, I>>(base?: I): T;`);
          commonStaticMembers.push((0, ts_poet_1.code)`fromPartial<I extends ${Exact}<${DeepPartial}<T>, I>>(object: I): T;`);
        } else {
          commonStaticMembers.push((0, ts_poet_1.code)`create(base?: DeepPartial<T>): T;`);
          commonStaticMembers.push((0, ts_poet_1.code)`fromPartial(object: DeepPartial<T>): T;`);
        }
      }
      const maybeExport = options.exportCommonSymbols ? "export" : "";
      const MessageFns = (0, ts_poet_1.conditionalOutput)("MessageFns", (0, ts_poet_1.code)`
      ${maybeExport} interface MessageFns<T${hasTypeMember ? ", V extends string" : ""}> {
        ${(0, ts_poet_1.joinCode)(commonStaticMembers, { on: "\n" })}
      }
    `);
      const ExtensionFns = (0, ts_poet_1.conditionalOutput)("ExtensionFns", (0, ts_poet_1.code)`
      ${maybeExport} interface ExtensionFns<T> {
        ${(0, ts_poet_1.joinCode)(extensionStaticMembers, { on: "\n" })}
      }
    `);
      const ExtensionHolder = (0, ts_poet_1.conditionalOutput)("ExtensionHolder", (0, ts_poet_1.code)`
      ${maybeExport} type ExtensionHolder<T extends string, V> = {
        [key in T]: Extension<V>;
      }
    `);
      const StructWrapperFns = (0, ts_poet_1.conditionalOutput)("StructWrapperFns", (0, ts_poet_1.code)`
      ${maybeExport} interface StructWrapperFns {
        wrap(object: {[key: string]: any} | undefined): ${(0, utils_12.wrapTypeName)(options, "Struct")};
        unwrap(message: ${(0, utils_12.wrapTypeName)(options, "Struct")}): {[key: string]: any};
      }
    `);
      const AnyValueWrapperFns = (0, ts_poet_1.conditionalOutput)("AnyValueWrapperFns", (0, ts_poet_1.code)`
      ${maybeExport} interface AnyValueWrapperFns {
        wrap(value: any): ${(0, utils_12.wrapTypeName)(options, "Value")};
        unwrap(message: any): string | number | boolean | Object | null | Array<any> | undefined;
      }
    `);
      const ListValueWrapperFns = (0, ts_poet_1.conditionalOutput)("ListValueWrapperFns", (0, ts_poet_1.code)`
      ${maybeExport} interface ListValueWrapperFns {
        wrap(array: ${options.useReadonlyTypes ? "Readonly" : ""}Array<any> | undefined): ${(0, utils_12.wrapTypeName)(options, "ListValue")};
        unwrap(message: ${options.useReadonlyTypes ? "any" : (0, utils_12.wrapTypeName)(options, "ListValue")}): Array<any>;
      }
    `);
      const FieldMaskWrapperFns = (0, ts_poet_1.conditionalOutput)("FieldMaskWrapperFns", (0, ts_poet_1.code)`
      ${maybeExport} interface FieldMaskWrapperFns {
        wrap(paths: ${options.useReadonlyTypes ? "readonly" : ""} string[]): ${(0, utils_12.wrapTypeName)(options, "FieldMask")};
        unwrap(message: ${options.useReadonlyTypes ? "any" : (0, utils_12.wrapTypeName)(options, "FieldMask")}): string[] ${options.useOptionals === "all" ? "| undefined" : ""};
      }
    `);
      return {
        MessageFns,
        ExtensionFns,
        ExtensionHolder,
        StructWrapperFns,
        AnyValueWrapperFns,
        ListValueWrapperFns,
        FieldMaskWrapperFns
      };
    }
    function makeObjectIdMethods() {
      const mongodb = (0, ts_poet_1.imp)("mongodb*mongodb");
      const fromProtoObjectId = (0, ts_poet_1.conditionalOutput)("fromProtoObjectId", (0, ts_poet_1.code)`
      function fromProtoObjectId(oid: ObjectId): ${mongodb}.ObjectId {
        return new ${mongodb}.ObjectId(oid.value);
      }
    `);
      const fromJsonObjectId = (0, ts_poet_1.conditionalOutput)("fromJsonObjectId", (0, ts_poet_1.code)`
      function fromJsonObjectId(o: any): ${mongodb}.ObjectId {
        if (o instanceof ${mongodb}.ObjectId) {
          return o;
        } else if (typeof o === "string") {
          return new ${mongodb}.ObjectId(o);
        } else {
          return ${fromProtoObjectId}(ObjectId.fromJSON(o));
        }
      }
    `);
      const toProtoObjectId = (0, ts_poet_1.conditionalOutput)("toProtoObjectId", (0, ts_poet_1.code)`
      function toProtoObjectId(oid: ${mongodb}.ObjectId): ObjectId {
        const value = oid.toString();
        return { value };
      }
    `);
      return { fromJsonObjectId, fromProtoObjectId, toProtoObjectId };
    }
    function makeTimestampMethods(options, longs, bytes) {
      const Timestamp = (0, utils_12.impProto)(options, "google/protobuf/timestamp", (0, utils_12.wrapTypeName)(options, "Timestamp"));
      const NanoDate = (0, ts_poet_1.imp)("NanoDate=nano-date");
      let seconds = "Math.trunc(date.getTime() / 1_000)";
      let toNumberCode = "t.seconds";
      const makeToNumberCode = (methodCall) => `t.seconds${options.useOptionals === "all" || options.noDefaultsForOptionals ? "?" : ""}.${methodCall}`;
      if (options.forceLong === options_12.LongOption.LONG) {
        toNumberCode = makeToNumberCode("toNumber()");
        seconds = (0, ts_poet_1.code)`${longs.numberToLong}(${seconds})`;
      } else if (options.forceLong === options_12.LongOption.BIGINT) {
        toNumberCode = (0, ts_poet_1.code)`${bytes.globalThis}.Number(${makeToNumberCode("toString()")})`;
        seconds = (0, ts_poet_1.code)`BigInt(${seconds})`;
      } else if (options.forceLong === options_12.LongOption.STRING) {
        toNumberCode = (0, ts_poet_1.code)`${bytes.globalThis}.Number(t.seconds)`;
        seconds = (0, ts_poet_1.code)`${seconds}.toString()`;
      }
      const maybeTypeField = (0, options_12.addTypeToMessages)(options) ? `$type: 'google.protobuf.Timestamp',` : "";
      const toTimestamp = (0, ts_poet_1.conditionalOutput)("toTimestamp", options.useDate === options_12.DateOption.STRING ? (0, ts_poet_1.code)`
          function toTimestamp(dateStr: string): ${Timestamp} {
            const date = new ${bytes.globalThis}.Date(dateStr);
            const seconds = ${seconds};
            const nanos = (date.getTime() % 1_000) * 1_000_000;
            return { ${maybeTypeField} seconds, nanos };
          }
        ` : options.useDate === options_12.DateOption.STRING_NANO ? (0, ts_poet_1.code)`
          function toTimestamp(dateStr: string): ${Timestamp} {
            const nanoDate = new ${NanoDate}(dateStr);

            const date = {
              getTime: (): number => nanoDate.valueOf(),
            } as const;
            const seconds = ${seconds};

            let nanos = nanoDate.getMilliseconds() * 1_000_000;
            nanos += nanoDate.getMicroseconds() * 1_000;
            nanos += nanoDate.getNanoseconds();

            return { ${maybeTypeField} seconds, nanos };
          }
        ` : options.useDate === options_12.DateOption.TEMPORAL ? (0, ts_poet_1.code)`
            function toTimestamp(instant: Temporal.Instant): ${Timestamp} {
              const date = {
                getTime: (): number => instant.epochMilliseconds,
              } as const;
              const seconds = ${seconds};
              const remainder = instant.round({ smallestUnit: "seconds",  roundingMode: "floor" }).until(instant);
              const nanos = (remainder.milliseconds * 1_000_000) + (remainder.microseconds * 1_000) + remainder.nanoseconds;

              return { ${maybeTypeField} seconds, nanos };
            }
          ` : (0, ts_poet_1.code)`
          function toTimestamp(date: Date): ${Timestamp} {
            const seconds = ${seconds};
            const nanos = (date.getTime() % 1_000) * 1_000_000;
            return { ${maybeTypeField} seconds, nanos };
          }
        `);
      const fromTimestamp = (0, ts_poet_1.conditionalOutput)("fromTimestamp", options.useDate === options_12.DateOption.STRING ? (0, ts_poet_1.code)`
          function fromTimestamp(t: ${Timestamp}): string {
            let millis = (${toNumberCode} || 0) * 1_000;
            millis += (t.nanos || 0) / 1_000_000;
            return new ${bytes.globalThis}.Date(millis).toISOString();
          }
        ` : options.useDate === options_12.DateOption.STRING_NANO ? (0, ts_poet_1.code)`
          function fromTimestamp(t: ${Timestamp}): string {
            const seconds = ${toNumberCode} || 0;
            const nanos = (t.nanos || 0) % 1_000;
            const micros = Math.trunc(((t.nanos || 0) % 1_000_000) / 1_000)
            let millis = seconds * 1_000;
            millis += Math.trunc((t.nanos || 0) / 1_000_000);

            const nanoDate = new ${NanoDate}(millis);
            nanoDate.setMicroseconds(micros);
            nanoDate.setNanoseconds(nanos);

            return nanoDate.toISOStringFull();
          }
        ` : options.useDate === options_12.DateOption.TEMPORAL ? (0, ts_poet_1.code)`
            function fromTimestamp(t: ${Timestamp}): Temporal.Instant {
              const seconds = ${toNumberCode} || 0;
              return ${bytes.globalThis}.Temporal.Instant
                .fromEpochMilliseconds(seconds * 1_000)
                .add(${bytes.globalThis}.Temporal.Duration.from({ nanoseconds: t.nanos }));
            }
          ` : (0, ts_poet_1.code)`
          function fromTimestamp(t: ${Timestamp}): Date {
            let millis = (${toNumberCode} || 0) * 1_000;
            millis += (t.nanos || 0) / 1_000_000;
            return new ${bytes.globalThis}.Date(millis);
          }
        `);
      const fromJsonTimestamp = (0, ts_poet_1.conditionalOutput)("fromJsonTimestamp", options.useDate === options_12.DateOption.DATE ? (0, ts_poet_1.code)`
        function fromJsonTimestamp(o: any): Date {
          if (o instanceof ${bytes.globalThis}.Date) {
            return o;
          } else if (typeof o === "string") {
            return new ${bytes.globalThis}.Date(o);
          } else {
            return ${fromTimestamp}(${(0, utils_12.wrapTypeName)(options, "Timestamp")}.fromJSON(o));
          }
        }
      ` : options.useDate === options_12.DateOption.TEMPORAL ? (0, ts_poet_1.code)`
          function fromJsonTimestamp(o: any): Temporal.Instant {
            if (o instanceof ${bytes.globalThis}.Date) {
              return ${bytes.globalThis}.Temporal.Instant.fromEpochMilliseconds(o.getTime());
            } else if (typeof o === "string") {
              return ${bytes.globalThis}.Temporal.Instant.from(o);
            } else {
              return ${fromTimestamp}(${(0, utils_12.wrapTypeName)(options, "Timestamp")}.fromJSON(o));
            }
          }
        ` : (0, ts_poet_1.code)`
        function fromJsonTimestamp(o: any): ${(0, utils_12.wrapTypeName)(options, "Timestamp")} {
          if (o instanceof ${bytes.globalThis}.Date) {
            return ${toTimestamp}(o);
          } else if (typeof o === "string") {
            return ${toTimestamp}(new ${bytes.globalThis}.Date(o));
          } else {
            return ${options.typePrefix}Timestamp${options.typeSuffix}.fromJSON(o);
          }
        }
      `);
      return { toTimestamp, fromTimestamp, fromJsonTimestamp };
    }
    function makeComparisonUtils() {
      const isObject = (0, ts_poet_1.conditionalOutput)("isObject", (0, ts_poet_1.code)`
    function isObject(value: any): boolean {
      return typeof value === 'object' && value !== null;
    }`);
      const isSet = (0, ts_poet_1.conditionalOutput)("isSet", (0, ts_poet_1.code)`
    function isSet(value: any): boolean {
      return value !== null && value !== undefined;
    }`);
      return { isObject, isSet };
    }
    function makeNiceGrpcServerStreamingMethodResult(options) {
      const NiceGrpcServerStreamingMethodResult = (0, ts_poet_1.conditionalOutput)("ServerStreamingMethodResult", options.outputIndex ? (0, ts_poet_1.code)`
        type ServerStreamingMethodResult<Response> = {
          [Symbol.asyncIterator](): AsyncIterator<Response, void>;
        };
      ` : (0, ts_poet_1.code)`
        export type ServerStreamingMethodResult<Response> = {
          [Symbol.asyncIterator](): AsyncIterator<Response, void>;
        };
      `);
      return { NiceGrpcServerStreamingMethodResult };
    }
    function makeGrpcWebErrorClass(bytes) {
      const GrpcWebError = (0, ts_poet_1.conditionalOutput)("GrpcWebError", (0, ts_poet_1.code)`
      export class GrpcWebError extends ${bytes.globalThis}.Error {
        constructor(message: string, public code: grpc.Code, public metadata: grpc.Metadata) {
          super(message);
        }
      }
    `);
      return { GrpcWebError };
    }
    function makeExtensionClass(options) {
      const Extension = (0, ts_poet_1.conditionalOutput)("Extension", (0, ts_poet_1.code)`
      export interface Extension <T> {
        number: number;
        tag: number;
        singularTag?: number;
        packedTag?: number;
        encode?: (message: T) => Uint8Array[];
        decode?: (tag: number, input: Uint8Array[]) => T;
        repeated: boolean;
        packed: boolean;
      }
    `);
      return { Extension };
    }
    function makeAssertionUtils(bytes) {
      const fail = (0, ts_poet_1.conditionalOutput)("fail", (0, ts_poet_1.code)`
      function fail(message?: string): never {
        throw new ${bytes.globalThis}.Error(message ?? "Failed");
      }
    `);
      return { fail };
    }
    function generateInterfaceDeclaration(ctx, fullName, messageDesc, sourceInfo, fullTypeName) {
      const { options, currentFile } = ctx;
      const chunks = [];
      (0, utils_12.maybeAddComment)(options, sourceInfo, chunks, messageDesc.options?.deprecated);
      chunks.push((0, ts_poet_1.code)`export interface ${(0, ts_poet_1.def)(fullName)} {`);
      if ((0, options_12.addTypeToMessages)(options)) {
        chunks.push((0, ts_poet_1.code)`$type${options.outputTypeAnnotations === "optional" ? "?" : ""}: '${fullTypeName}',`);
      }
      const processedOneofs = /* @__PURE__ */ new Set();
      messageDesc.field.forEach((fieldDesc, index) => {
        if ((0, types_12.isWithinOneOfThatShouldBeUnion)(options, fieldDesc)) {
          const { oneofIndex } = fieldDesc;
          if (!processedOneofs.has(oneofIndex)) {
            processedOneofs.add(oneofIndex);
            chunks.push(generateOneofProperty(ctx, messageDesc, oneofIndex, sourceInfo));
          }
          return;
        }
        const info = sourceInfo.lookup(sourceInfo_1.Fields.message.field, index);
        (0, utils_12.maybeAddComment)(options, info, chunks, fieldDesc.options?.deprecated);
        const fieldKey = (0, utils_12.safeAccessor)((0, utils_12.getFieldName)(fieldDesc, options));
        const isOptional = (0, types_12.isOptionalProperty)(fieldDesc, messageDesc.options, options, currentFile.isProto3Syntax);
        const type = (0, types_12.toTypeName)(ctx, messageDesc, fieldDesc, isOptional);
        chunks.push((0, ts_poet_1.code)`${maybeReadonly(options)}${fieldKey}${isOptional ? "?" : ""}: ${type}, `);
      });
      if (ctx.options.unknownFields) {
        chunks.push((0, ts_poet_1.code)`_unknownFields?: {[key: number]: Uint8Array[]} | undefined,`);
      }
      chunks.push((0, ts_poet_1.code)`}`);
      return (0, ts_poet_1.joinCode)(chunks, { on: "\n" });
    }
    function generateOneofProperty(ctx, messageDesc, oneofIndex, sourceInfo) {
      const { options } = ctx;
      const fields = messageDesc.field.map((field, index) => ({ index, field })).filter((item) => (0, types_12.isWithinOneOf)(item.field) && item.field.oneofIndex === oneofIndex);
      const mbReadonly = maybeReadonly(options);
      const info = sourceInfo.lookup(sourceInfo_1.Fields.message.oneof_decl, oneofIndex);
      let outerComments = [];
      (0, utils_12.maybeAddComment)(options, info, outerComments);
      const unionType = (0, ts_poet_1.joinCode)(fields.flatMap((f) => {
        const fieldInfo = sourceInfo.lookup(sourceInfo_1.Fields.message.field, f.index);
        let fieldName = (0, case_1.maybeSnakeToCamel)(f.field.name, options);
        let typeName = (0, types_12.toTypeName)(ctx, messageDesc, f.field);
        let valueName = (0, utils_12.oneofValueName)(fieldName, options);
        let fieldComments = [];
        (0, utils_12.maybeAddComment)(options, fieldInfo, fieldComments);
        const combinedComments = fieldComments.join("\n");
        return (0, ts_poet_1.code)`|${combinedComments ? " // " : ""}\n ${combinedComments} { ${mbReadonly}$case: '${fieldName}', ${mbReadonly}${valueName}: ${typeName} }`;
      }));
      const name = (0, case_1.maybeSnakeToCamel)(messageDesc.oneofDecl[oneofIndex].name, options);
      const optionalFlag = options.useOptionals === "none" ? "" : "?";
      return (0, ts_poet_1.joinCode)([...outerComments, (0, ts_poet_1.code)`${mbReadonly}${name}${optionalFlag}:`, unionType, (0, ts_poet_1.code)`| ${(0, utils_12.nullOrUndefined)(options)},`], {
        on: "\n"
      });
    }
    function generateBaseInstanceFactory(ctx, fullName, messageDesc, fullTypeName) {
      const { options, currentFile } = ctx;
      const fields = [];
      const processedOneofs = /* @__PURE__ */ new Set();
      for (const field of messageDesc.field) {
        if ((0, types_12.isWithinOneOfThatShouldBeUnion)(ctx.options, field)) {
          const { oneofIndex } = field;
          if (!processedOneofs.has(oneofIndex)) {
            processedOneofs.add(oneofIndex);
            const name = options.useJsonName ? (0, utils_12.getFieldName)(field, options) : (0, case_1.maybeSnakeToCamel)(messageDesc.oneofDecl[oneofIndex].name, ctx.options);
            fields.push((0, ts_poet_1.code)`${(0, utils_12.safeAccessor)(name)}: ${(0, utils_12.nullOrUndefined)(options)}`);
          }
          continue;
        }
        if (!options.initializeFieldsAsUndefined && (0, types_12.isOptionalProperty)(field, messageDesc.options, options, currentFile.isProto3Syntax)) {
          continue;
        }
        const fieldKey = (0, utils_12.safeAccessor)((0, utils_12.getFieldName)(field, options));
        const val = (0, types_12.isWithinOneOf)(field) ? (0, utils_12.nullOrUndefined)(options) : (0, types_12.isMapType)(ctx, messageDesc, field) ? (0, types_12.shouldGenerateJSMapType)(ctx, messageDesc, field) ? "new Map()" : "{}" : (0, types_12.isRepeated)(field) ? "[]" : (0, types_12.defaultValue)(ctx, field);
        fields.push((0, ts_poet_1.code)`${fieldKey}: ${val}`);
      }
      if ((0, options_12.addTypeToMessages)(options)) {
        fields.unshift((0, ts_poet_1.code)`$type: '${fullTypeName}'`);
      }
      if (ctx.options.unknownFields && ctx.options.initializeFieldsAsUndefined) {
        fields.push((0, ts_poet_1.code)`_unknownFields: {}`);
      }
      return (0, ts_poet_1.code)`
    function createBase${fullName}(): ${fullName} {
      return { ${(0, ts_poet_1.joinCode)(fields, { on: "," })} };
    }
  `;
    }
    function getDecodeReadSnippet(ctx, field) {
      const { options, utils } = ctx;
      let readSnippet;
      if ((0, types_12.isPrimitive)(field)) {
        readSnippet = (0, ts_poet_1.code)`reader.${(0, types_12.toReaderCall)(field)}()`;
        if ((0, types_12.isBytes)(field)) {
          if (options.env === options_12.EnvOption.NODE) {
            readSnippet = (0, ts_poet_1.code)`Buffer.from(${readSnippet})`;
          }
        } else if ((0, types_12.basicLongWireType)(field.type) !== void 0) {
          if ((0, types_12.isJsTypeFieldOption)(options, field)) {
            switch (field.options.jstype) {
              case ts_proto_descriptors_12.FieldOptions_JSType.JS_NUMBER:
                readSnippet = (0, ts_poet_1.code)`${utils.longToNumber}(${readSnippet})`;
                break;
              case ts_proto_descriptors_12.FieldOptions_JSType.JS_STRING:
                readSnippet = (0, ts_poet_1.code)`${readSnippet}.toString()`;
                break;
            }
          } else if (options.forceLong === options_12.LongOption.LONG) {
            switch (field.type) {
              case ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_UINT64:
              case ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_FIXED64:
                readSnippet = (0, ts_poet_1.code)`${utils.Long}.fromString(${readSnippet}.toString(), true)`;
                break;
              default:
                readSnippet = (0, ts_poet_1.code)`${utils.Long}.fromString(${readSnippet}.toString())`;
                break;
            }
          } else if (options.forceLong === options_12.LongOption.STRING) {
            readSnippet = (0, ts_poet_1.code)`${readSnippet}.toString()`;
          } else if (options.forceLong === options_12.LongOption.BIGINT) {
            readSnippet = (0, ts_poet_1.code)`${readSnippet} as bigint`;
          } else {
            readSnippet = (0, ts_poet_1.code)`${utils.longToNumber}(${readSnippet})`;
          }
        } else if ((0, types_12.isEnum)(field)) {
          if (options.stringEnums) {
            const fromJson = (0, types_12.getEnumMethod)(ctx, field.typeName, "FromJSON");
            readSnippet = (0, ts_poet_1.code)`${fromJson}(${readSnippet})`;
          } else {
            readSnippet = (0, ts_poet_1.code)`${readSnippet} as any`;
          }
        }
      } else if ((0, types_12.isValueType)(ctx, field)) {
        const type = (0, types_12.basicTypeName)(ctx, field, { keepValueType: true });
        const unwrap = (decodedValue) => {
          if ((0, types_12.isListValueType)(field) || (0, types_12.isStructType)(field) || (0, types_12.isAnyValueType)(field) || (0, types_12.isFieldMaskType)(field)) {
            return (0, ts_poet_1.code)`${type}.unwrap(${decodedValue})`;
          }
          return (0, ts_poet_1.code)`${decodedValue}.value`;
        };
        const decoder = (0, ts_poet_1.code)`${type}.decode(reader, reader.uint32())`;
        readSnippet = (0, ts_poet_1.code)`${unwrap(decoder)}`;
      } else if ((0, types_12.isTimestamp)(field) && (options.useDate === options_12.DateOption.DATE || options.useDate === options_12.DateOption.STRING || options.useDate === options_12.DateOption.STRING_NANO || options.useDate === options_12.DateOption.TEMPORAL)) {
        const type = (0, types_12.basicTypeName)(ctx, field, { keepValueType: true });
        readSnippet = (0, ts_poet_1.code)`${utils.fromTimestamp}(${type}.decode(reader, reader.uint32()))`;
      } else if ((0, types_12.isObjectId)(field) && options.useMongoObjectId) {
        const type = (0, types_12.basicTypeName)(ctx, field, { keepValueType: true });
        readSnippet = (0, ts_poet_1.code)`${utils.fromProtoObjectId}(${type}.decode(reader, reader.uint32()))`;
      } else if ((0, types_12.isMessage)(field)) {
        const type = (0, types_12.basicTypeName)(ctx, field);
        if (field.type == ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_GROUP) {
          readSnippet = (0, ts_poet_1.code)`${type}.decode(reader)`;
        } else {
          readSnippet = (0, ts_poet_1.code)`${type}.decode(reader, reader.uint32())`;
        }
      } else {
        throw new Error(`Unhandled field ${field}`);
      }
      return readSnippet;
    }
    function generateEmptyDecode(fullName) {
      const BinaryReader = (0, ts_poet_1.imp)("t:BinaryReader@@bufbuild/protobuf/wire");
      return (0, ts_poet_1.code)`
    decode(
      _: ${BinaryReader} | Uint8Array,
      length?: number,
    ): ${fullName} {
      throw new Error('decode not generated for ${fullName}');
    }
  `;
    }
    function generateDecode(ctx, fullName, messageDesc) {
      const { options, currentFile } = ctx;
      const chunks = [];
      let createBase = (0, ts_poet_1.code)`createBase${fullName}()`;
      if (options.usePrototypeForDefaults) {
        createBase = (0, ts_poet_1.code)`Object.create(${createBase}) as ${fullName}`;
      }
      const BinaryReader = (0, ts_poet_1.imp)("BinaryReader@@bufbuild/protobuf/wire");
      chunks.push((0, ts_poet_1.code)`
    decode(
      input: ${BinaryReader} | Uint8Array,
      length?: number,
    ): ${fullName} {
      const reader = input instanceof ${BinaryReader} ? input : new ${BinaryReader}(input);
      const previousRecursionDepth = (reader as any).__tsProtoDecodeDepth ?? 0;
      if (previousRecursionDepth >= 100) {
        throw new ${ctx.utils.globalThis}.Error("protobuf decode recursion limit exceeded");
      }
      (reader as any).__tsProtoDecodeDepth = previousRecursionDepth + 1;
      try {
        const end = length === undefined ? reader.len : reader.pos + length;
  `);
      chunks.push((0, ts_poet_1.code)`const message = ${createBase}${maybeAsAny(options)};`);
      chunks.push((0, ts_poet_1.code)`
    while (reader.pos < end) {
      const tag = reader.uint32();
      switch (tag >>> 3) {
  `);
      messageDesc.field.forEach((field) => {
        const fieldName = (0, utils_12.getFieldName)(field, options);
        const messageProperty = (0, utils_12.getPropertyAccessor)("message", fieldName);
        chunks.push((0, ts_poet_1.code)`case ${field.number}: {`);
        const tag = (field.number << 3 | (0, types_12.basicWireType)(field.type)) >>> 0;
        const tagCheck = (0, ts_poet_1.code)`
      if (tag !== ${tag}) {
        break;
      }
    `;
        const readSnippet = getDecodeReadSnippet(ctx, field);
        const initializerNecessary = !options.initializeFieldsAsUndefined && (0, types_12.isOptionalProperty)(field, messageDesc.options, options, currentFile.isProto3Syntax);
        if ((0, types_12.isRepeated)(field)) {
          const maybeNonNullAssertion = ctx.options.useOptionals === "all" || ctx.options.useOptionals === "deprecatedOnly" ? "!" : "";
          const mapType = (0, types_12.detectMapType)(ctx, messageDesc, field);
          if (mapType) {
            const varName = `entry${field.number}`;
            const generateMapType = (0, types_12.shouldGenerateJSMapType)(ctx, messageDesc, field);
            let valueSetterSnippet;
            if (generateMapType) {
              valueSetterSnippet = `${messageProperty}${maybeNonNullAssertion}.set(${varName}.key, ${varName}.value)`;
            } else {
              valueSetterSnippet = `${messageProperty}${maybeNonNullAssertion}[${varName}.key] = ${varName}.value`;
            }
            const initializerSnippet = initializerNecessary ? `
            if (${messageProperty} === undefined ${(0, utils_12.withOrMaybeCheckIsNull)(options, messageProperty)}) {
              ${messageProperty} = ${generateMapType ? "new Map()" : "{}"};
            }` : "";
            const ifValueCheck = `${varName}.value !== undefined ${(0, utils_12.withAndMaybeCheckIsNotNull)(options, `${varName}.value`)}`;
            const maybeIfKeyCheck = `${options.noDefaultsForOptionals ? ` && ${varName}.key !== undefined ${(0, utils_12.withAndMaybeCheckIsNotNull)(options, `${varName}.key`)}` : ""}`;
            chunks.push((0, ts_poet_1.code)`
          ${tagCheck}
          const ${varName} = ${readSnippet};
          if (${ifValueCheck}${maybeIfKeyCheck}) {
            ${initializerSnippet}
            ${valueSetterSnippet};
          }
        `);
          } else {
            const initializerSnippet = initializerNecessary ? `
            if (${messageProperty} === undefined ${(0, utils_12.withOrMaybeCheckIsNull)(options, messageProperty)}) {
              ${messageProperty} = [];
            }` : "";
            if ((0, types_12.packedType)(field.type) === void 0) {
              if (options.useOptionals === "all") {
                chunks.push((0, ts_poet_1.code)`
              ${tagCheck}
              ${initializerSnippet}
              const el = ${readSnippet};
              if (el !== undefined) {
                ${messageProperty}${maybeNonNullAssertion}.push(el);
              }
            `);
              } else {
                chunks.push((0, ts_poet_1.code)`
              ${tagCheck}
              ${initializerSnippet}
              ${messageProperty}${maybeNonNullAssertion}.push(${readSnippet});
            `);
              }
            } else {
              const packedTag = (field.number << 3 | 2) >>> 0;
              chunks.push((0, ts_poet_1.code)`
            if (tag === ${tag}) {
              ${initializerSnippet}
              ${messageProperty}${maybeNonNullAssertion}.push(${readSnippet});

              continue;
            }

            if (tag === ${packedTag}) {
              ${initializerSnippet}
              const end2 = reader.uint32() + reader.pos;
              while (reader.pos < end2) {
                ${messageProperty}${maybeNonNullAssertion}.push(${readSnippet});
              }

              continue;
            }

            break;
          }`);
            }
          }
        } else if ((0, types_12.isWithinOneOfThatShouldBeUnion)(options, field)) {
          const oneofNameWithMessage = options.useJsonName ? messageProperty : (0, utils_12.getPropertyAccessor)("message", (0, case_1.maybeSnakeToCamel)(messageDesc.oneofDecl[field.oneofIndex].name, options));
          const valueName = (0, utils_12.oneofValueName)(fieldName, options);
          chunks.push((0, ts_poet_1.code)`
        ${tagCheck}
        ${oneofNameWithMessage} = { $case: '${fieldName}', ${valueName}: ${readSnippet} };
      `);
        } else {
          chunks.push((0, ts_poet_1.code)`
        ${tagCheck}
        ${messageProperty} = ${readSnippet};
      `);
        }
        if (!(0, types_12.isRepeated)(field) || (0, types_12.packedType)(field.type) === void 0) {
          chunks.push((0, ts_poet_1.code)`continue; }`);
        }
      });
      chunks.push((0, ts_poet_1.code)`}`);
      chunks.push((0, ts_poet_1.code)`
      if ((tag & 7) === 4 || tag === 0) {
        break;
      }
  `);
      if (options.unknownFields) {
        let unknownFieldsInitializerSnippet = "";
        let maybeNonNullAssertion = options.initializeFieldsAsUndefined ? "!" : "";
        if (!options.initializeFieldsAsUndefined) {
          unknownFieldsInitializerSnippet = `
        if (message._unknownFields === undefined ${(0, utils_12.withOrMaybeCheckIsNull)(options, `message._unknownFields`)}) {
          message._unknownFields = {};
        }
      `;
        }
        chunks.push((0, ts_poet_1.code)`
      const buf = reader.skip(tag & 7);

      ${unknownFieldsInitializerSnippet}
      const list = message._unknownFields${maybeNonNullAssertion}[tag];

      if (list === undefined ${(0, utils_12.withOrMaybeCheckIsNull)(options, `message._unknownFields`)}) {
        message._unknownFields${maybeNonNullAssertion}[tag] = [buf];
      } else {
        list.push(buf);
      }
    `);
      } else {
        chunks.push((0, ts_poet_1.code)`
        reader.skip(tag & 7);
    `);
      }
      chunks.push((0, ts_poet_1.code)`}`);
      chunks.push((0, ts_poet_1.code)`return message;`);
      chunks.push((0, ts_poet_1.code)`
      } finally {
        (reader as any).__tsProtoDecodeDepth = previousRecursionDepth;
      }
    }
  `);
      return (0, ts_poet_1.joinCode)(chunks, { on: "\n" });
    }
    function getEncodeWriteSnippet(ctx, field) {
      const { options, utils } = ctx;
      if ((0, types_12.isEnum)(field) && options.stringEnums) {
        const tag = (field.number << 3 | (0, types_12.basicWireType)(field.type)) >>> 0;
        const toNumber = (0, types_12.getEnumMethod)(ctx, field.typeName, "ToNumber");
        return (place) => (0, ts_poet_1.code)`writer.uint32(${tag}).${(0, types_12.toReaderCall)(field)}(${toNumber}(${place}))`;
      } else if ((0, types_12.isLong)(field) && options.forceLong === options_12.LongOption.BIGINT) {
        const tag = (field.number << 3 | (0, types_12.basicWireType)(field.type)) >>> 0;
        const fieldType = (0, types_12.toReaderCall)(field);
        switch (fieldType) {
          case "int64":
          case "sint64":
          case "sfixed64":
            return (place, placeAlt) => (0, ts_poet_1.code)`if (BigInt.asIntN(64, ${place}) !== ${placeAlt ?? place}) {
          throw new ${utils.globalThis}.Error('value provided for field ${place} of type ${fieldType} too large');
        }
        writer.uint32(${tag}).${(0, types_12.toReaderCall)(field)}(${place})`;
          case "uint64":
          case "fixed64":
            return (place, placeAlt) => (0, ts_poet_1.code)`if (BigInt.asUintN(64, ${place}) !== ${placeAlt ?? place}) {
          throw new ${utils.globalThis}.Error('value provided for field ${place} of type ${fieldType} too large');
        }
        writer.uint32(${tag}).${(0, types_12.toReaderCall)(field)}(${place})`;
          default:
            throw new Error(`unexpected BigInt type: ${fieldType}`);
        }
      } else if ((0, types_12.isScalar)(field) || (0, types_12.isEnum)(field)) {
        const tag = (field.number << 3 | (0, types_12.basicWireType)(field.type)) >>> 0;
        if ((0, types_12.isLong)(field) && options.forceLong === options_12.LongOption.LONG) {
          return (place) => (0, ts_poet_1.code)`writer.uint32(${tag}).${(0, types_12.toReaderCall)(field)}(${place}.toString())`;
        }
        return (place) => (0, ts_poet_1.code)`writer.uint32(${tag}).${(0, types_12.toReaderCall)(field)}(${place})`;
      } else if ((0, types_12.isObjectId)(field) && options.useMongoObjectId) {
        const tag = (field.number << 3 | 2) >>> 0;
        const type = (0, types_12.basicTypeName)(ctx, field, { keepValueType: true });
        return (place) => (0, ts_poet_1.code)`${type}.encode(${utils.toProtoObjectId}(${place}), writer.uint32(${tag}).fork()).join()`;
      } else if ((0, types_12.isTimestamp)(field) && (options.useDate === options_12.DateOption.DATE || options.useDate === options_12.DateOption.STRING || options.useDate === options_12.DateOption.STRING_NANO || options.useDate === options_12.DateOption.TEMPORAL)) {
        const tag = (field.number << 3 | 2) >>> 0;
        const type = (0, types_12.basicTypeName)(ctx, field, { keepValueType: true });
        return (place) => (0, ts_poet_1.code)`${type}.encode(${utils.toTimestamp}(${place}), writer.uint32(${tag}).fork()).join()`;
      } else if ((0, types_12.isValueType)(ctx, field)) {
        const maybeTypeField = (0, options_12.addTypeToMessages)(options) ? `$type: '${field.typeName.slice(1)}',` : "";
        const type = (0, types_12.basicTypeName)(ctx, field, { keepValueType: true });
        const wrappedValue = (place) => {
          if ((0, types_12.isAnyValueType)(field) || (0, types_12.isListValueType)(field) || (0, types_12.isStructType)(field) || (0, types_12.isFieldMaskType)(field)) {
            return (0, ts_poet_1.code)`${type}.wrap(${place})`;
          }
          return (0, ts_poet_1.code)`{${maybeTypeField} value: ${place}!}`;
        };
        const tag = (field.number << 3 | 2) >>> 0;
        return (place) => (0, ts_poet_1.code)`${type}.encode(${wrappedValue(place)}, writer.uint32(${tag}).fork()).join()`;
      } else if ((0, types_12.isMessage)(field)) {
        const type = (0, types_12.basicTypeName)(ctx, field);
        if (field.type == ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_GROUP) {
          const startTag = (field.number << 3 | 3) >>> 0, endTag = (field.number << 3 | 4) >>> 0;
          return (place) => (0, ts_poet_1.code)`${type}.encode(${place}, writer.uint32(${startTag})).uint32(${endTag})`;
        }
        const tag = (field.number << 3 | 2) >>> 0;
        return (place) => (0, ts_poet_1.code)`${type}.encode(${place}, writer.uint32(${tag}).fork()).join()`;
      } else {
        throw new Error(`Unhandled field ${field}`);
      }
    }
    function generateEmptyEncode(fullName) {
      const BinaryWriter = (0, ts_poet_1.imp)("BinaryWriter@@bufbuild/protobuf/wire");
      return (0, ts_poet_1.code)`
  encode(
    _: ${fullName},
    writer: ${BinaryWriter} = new ${BinaryWriter}(),
  ): ${BinaryWriter} {
    throw new Error('encode not generated for ${fullName}');
  }
  `;
    }
    function generateEncode(ctx, fullName, messageDesc) {
      const { options, utils, typeMap, currentFile } = ctx;
      const chunks = [];
      const BinaryWriter = (0, ts_poet_1.imp)("BinaryWriter@@bufbuild/protobuf/wire");
      chunks.push((0, ts_poet_1.code)`
    encode(
      ${messageDesc.field.length > 0 || options.unknownFields ? "message" : "_"}: ${fullName},
      writer: ${BinaryWriter} = new ${BinaryWriter}(),
    ): ${BinaryWriter} {
  `);
      const processedOneofs = /* @__PURE__ */ new Set();
      const oneOfFieldsDict = messageDesc.field.filter((field) => (0, types_12.isWithinOneOfThatShouldBeUnion)(options, field)).reduce((result, field) => ((result[field.oneofIndex] || (result[field.oneofIndex] = [])).push(field), result), {});
      messageDesc.field.forEach((field) => {
        const fieldName = (0, utils_12.getFieldName)(field, options);
        const messageProperty = (0, utils_12.getPropertyAccessor)("message", fieldName);
        const writeSnippet = getEncodeWriteSnippet(ctx, field);
        const isOptional = (0, types_12.isOptionalProperty)(field, messageDesc.options, options, currentFile.isProto3Syntax);
        if ((0, types_12.isRepeated)(field)) {
          if ((0, types_12.isMapType)(ctx, messageDesc, field)) {
            const mapInfo = (0, types_12.detectMapType)(ctx, messageDesc, field);
            const valueType = mapInfo.valueField;
            const maybeTypeField = (0, options_12.addTypeToMessages)(options) ? `$type: '${field.typeName.slice(1)}',` : "";
            const entryWriteSnippet = (0, types_12.isValueType)(ctx, valueType) ? (0, ts_poet_1.code)`
              if (value !== undefined ${(0, utils_12.withOrMaybeCheckIsNotNull)(options, `value`)}) {
                ${writeSnippet(`{ ${maybeTypeField} key: key as any, value }`)};
              }
            ` : writeSnippet(`{ ${maybeTypeField} key: key as any, value }`);
            const useMapType = (0, types_12.shouldGenerateJSMapType)(ctx, messageDesc, field);
            const optionalAlternative = isOptional ? useMapType ? " || new Map()" : " || {}" : "";
            if (useMapType) {
              chunks.push((0, ts_poet_1.code)`
            (${messageProperty}${optionalAlternative}).forEach((value, key) => {
              ${entryWriteSnippet}
            });
          `);
            } else {
              chunks.push((0, ts_poet_1.code)`
            ${utils.globalThis}.Object.entries(${messageProperty}${optionalAlternative}).forEach(([key, value]: [string, ${mapInfo.valueType}]) => {
              ${entryWriteSnippet}
            });
          `);
            }
          } else if ((0, types_12.packedField)(field, currentFile.isProto3Syntax) === void 0) {
            const listWriteSnippet = (0, ts_poet_1.code)`
          for (const v of ${messageProperty}) {
            ${writeSnippet("v!")};
          }
        `;
            if (isOptional) {
              chunks.push((0, ts_poet_1.code)`
            if (${messageProperty} !== undefined && ${messageProperty}.length !== 0) {
              ${listWriteSnippet}
            }
          `);
            } else {
              chunks.push(listWriteSnippet);
            }
          } else if ((0, types_12.isEnum)(field) && options.stringEnums) {
            const tag = (field.number << 3 | 2) >>> 0;
            const toNumber = (0, types_12.getEnumMethod)(ctx, field.typeName, "ToNumber");
            const listWriteSnippet = (0, ts_poet_1.code)`
          writer.uint32(${tag}).fork();
          for (const v of ${messageProperty}) {
            writer.${(0, types_12.toReaderCall)(field)}(${toNumber}(v));
          }
          writer.join();
        `;
            if (isOptional) {
              chunks.push((0, ts_poet_1.code)`
            if (${messageProperty} !== undefined && ${messageProperty}.length !== 0) {
              ${listWriteSnippet}
            }
          `);
            } else {
              chunks.push(listWriteSnippet);
            }
          } else {
            const tag = (field.number << 3 | 2) >>> 0;
            const rhs = (x) => (0, types_12.isLong)(field) && options.forceLong === options_12.LongOption.LONG ? `${x}.toString()` : x;
            let listWriteSnippet = (0, ts_poet_1.code)`
          writer.uint32(${tag}).fork();
          for (const v of ${messageProperty}) {
            writer.${(0, types_12.toReaderCall)(field)}(${rhs("v")});
          }
          writer.join();
        `;
            if ((0, types_12.isLong)(field) && options.forceLong === options_12.LongOption.BIGINT) {
              const fieldType = (0, types_12.toReaderCall)(field);
              switch (fieldType) {
                case "int64":
                case "sint64":
                case "sfixed64":
                  listWriteSnippet = (0, ts_poet_1.code)`
                writer.uint32(${tag}).fork();
                for (const v of ${messageProperty}) {
                  if (BigInt.asIntN(64, v) !== v) {
                    throw new ${utils.globalThis}.Error('a value provided in array field ${fieldName} of type ${fieldType} is too large');
                  }
                  writer.${(0, types_12.toReaderCall)(field)}(${rhs("v")});
                }
                writer.join();
              `;
                  break;
                case "uint64":
                case "fixed64":
                  listWriteSnippet = (0, ts_poet_1.code)`
                writer.uint32(${tag}).fork();
                for (const v of ${messageProperty}) {
                  if (BigInt.asUintN(64, v) !== v) {
                    throw new ${utils.globalThis}.Error('a value provided in array field ${fieldName} of type ${fieldType} is too large');
                  }
                  writer.${(0, types_12.toReaderCall)(field)}(${rhs("v")});
                }
                writer.join();
              `;
                  break;
                default:
                  throw new Error(`unexpected BigInt type: ${fieldType}`);
              }
            }
            if (isOptional) {
              chunks.push((0, ts_poet_1.code)`
            if (${messageProperty} !== undefined ${(0, utils_12.withAndMaybeCheckIsNotNull)(options, messageProperty)} && ${messageProperty}.length !== 0) {
              ${listWriteSnippet}
            }
          `);
            } else {
              chunks.push(listWriteSnippet);
            }
          }
        } else if ((0, types_12.isWithinOneOfThatShouldBeUnion)(options, field)) {
          if (!processedOneofs.has(field.oneofIndex)) {
            processedOneofs.add(field.oneofIndex);
            const oneofNameWithMessage = options.useJsonName ? messageProperty : (0, utils_12.getPropertyAccessor)("message", (0, case_1.maybeSnakeToCamel)(messageDesc.oneofDecl[field.oneofIndex].name, options));
            chunks.push((0, ts_poet_1.code)`switch (${oneofNameWithMessage}?.$case) {`);
            for (const oneOfField of oneOfFieldsDict[field.oneofIndex]) {
              const writeSnippet2 = getEncodeWriteSnippet(ctx, oneOfField);
              const oneOfFieldName = (0, case_1.maybeSnakeToCamel)(oneOfField.name, ctx.options);
              const valueName = (0, utils_12.oneofValueName)(oneOfFieldName, ctx.options);
              chunks.push((0, ts_poet_1.code)`case "${oneOfFieldName}":
            ${writeSnippet2(`${oneofNameWithMessage}.${valueName}`)};
            break;`);
            }
            chunks.push((0, ts_poet_1.code)`}`);
          }
        } else if ((0, types_12.isWithinOneOf)(field)) {
          chunks.push((0, ts_poet_1.code)`
        if (${messageProperty} !== undefined ${(0, utils_12.withAndMaybeCheckIsNotNull)(options, messageProperty)}) {
          ${writeSnippet(`${messageProperty}`)};
        }
      `);
        } else if ((0, types_12.isMessage)(field)) {
          chunks.push((0, ts_poet_1.code)`
        if (${messageProperty} !== undefined ${(0, utils_12.withAndMaybeCheckIsNotNull)(options, messageProperty)}) {
          ${writeSnippet(`${messageProperty}`)};
        }
      `);
        } else if ((0, types_12.isScalar)(field) || (0, types_12.isEnum)(field)) {
          const isJsType = (0, types_12.isScalar)(field) && (0, types_12.isJsTypeFieldOption)(options, field);
          const body = isJsType && options.forceLong === options_12.LongOption.BIGINT ? writeSnippet(`BigInt(${messageProperty})`) : writeSnippet(`${messageProperty}`);
          chunks.push((0, ts_poet_1.code)`
        if (${(0, types_12.notDefaultCheck)(ctx, field, messageDesc.options, `${messageProperty}`)}) {
          ${body};
        }
      `);
        } else {
          chunks.push((0, ts_poet_1.code)`${writeSnippet(`${messageProperty}`)};`);
        }
      });
      if (options.unknownFields) {
        chunks.push((0, ts_poet_1.code)`if (message._unknownFields !== undefined) {
      for (const [key, values] of ${utils.globalThis}.Object.entries(message._unknownFields)) {
        const tag = parseInt(key, 10);
        for (const value of values) {
          writer.uint32(tag).raw(value);
        }
      }
    }`);
      }
      chunks.push((0, ts_poet_1.code)`return writer;`);
      chunks.push((0, ts_poet_1.code)`}`);
      return (0, ts_poet_1.joinCode)(chunks, { on: "\n" });
    }
    function generateSetExtension(ctx, fullName) {
      return (0, ts_poet_1.code)`
    setExtension <T> (message: ${fullName}, extension: ${ctx.utils.Extension}<T>, value: T): void {
      const encoded = extension.encode!(value);

      if (message._unknownFields !== undefined) {
        delete message._unknownFields[extension.tag];

        if (extension.singularTag !== undefined) {
          delete message._unknownFields[extension.singularTag];
        }
        if (extension.packedTag !== undefined) {
          delete message._unknownFields[extension.packedTag];
        }
      }

      if (encoded.length !== 0) {
        if (message._unknownFields === undefined) {
          message._unknownFields = {};
        }

        message._unknownFields[extension.tag] = encoded;
      }
    }
  `;
    }
    function generateGetExtension(ctx, fullName) {
      return (0, ts_poet_1.code)`
    getExtension <T> (message: ${fullName}, extension: ${ctx.utils.Extension}<T>): T | undefined {
      let results: T | undefined = undefined;

      if (message._unknownFields === undefined) {
        return undefined;
      }

      let list = message._unknownFields[extension.tag];

      if (list !== undefined) {
        results = extension.decode!(extension.tag, list);
      }

      if (extension.singularTag === undefined ||
          extension.packedTag === undefined) {
        return results;
      }

      const nonDefaultTag = (extension.singularTag === extension.tag) ?
          extension.packedTag : extension.singularTag;
      list = message._unknownFields[nonDefaultTag];

      if (list !== undefined) {
        const results2 = extension.decode!(nonDefaultTag, list);

        if (results !== undefined && (results as any).length !== 0) {
          results = (results as any).concat(results2);
        } else {
          results = results2;
        }
      }

      return results;
    }
  `;
    }
    function generateExtension(ctx, message, extension) {
      const type = (0, types_12.toTypeName)(ctx, message, extension);
      const { currentFile } = ctx;
      const packedTag = (0, types_12.isRepeated)(extension) && (0, types_12.packedType)(extension.type) !== void 0 ? (extension.number << 3 | 2) >>> 0 : void 0;
      const singularTag = (extension.number << 3 | (0, types_12.basicWireType)(extension.type)) >>> 0;
      const packed = (0, types_12.isRepeated)(extension) && (0, types_12.packedField)(extension, currentFile.isProto3Syntax);
      const tag = packed ? packedTag : singularTag;
      const chunks = [];
      chunks.push((0, ts_poet_1.code)`{`);
      chunks.push((0, ts_poet_1.code)`number: ${extension.number},`);
      chunks.push((0, ts_poet_1.code)`tag: ${tag},`);
      if (packedTag !== void 0) {
        chunks.push((0, ts_poet_1.code)`singularTag: ${singularTag},`);
        chunks.push((0, ts_poet_1.code)`packedTag: ${packedTag},`);
      }
      chunks.push((0, ts_poet_1.code)`repeated: ${extension.label == ts_proto_descriptors_12.FieldDescriptorProto_Label.LABEL_REPEATED},`);
      chunks.push((0, ts_poet_1.code)`packed: ${extension.options?.packed ? true : false},`);
      const BinaryReader = (0, ts_poet_1.imp)("BinaryReader@@bufbuild/protobuf/wire");
      const BinaryWriter = (0, ts_poet_1.imp)("BinaryWriter@@bufbuild/protobuf/wire");
      if (ctx.options.outputEncodeMethods === true || ctx.options.outputEncodeMethods === "encode-only" || ctx.options.outputEncodeMethods === "encode-no-creation") {
        let getEncodeSnippet = function(ctx2, field) {
          const { options, utils } = ctx2;
          if ((0, types_12.isEnum)(field) && options.stringEnums) {
            const toNumber = (0, types_12.getEnumMethod)(ctx2, field.typeName, "ToNumber");
            return (place) => (0, ts_poet_1.code)`writer.${(0, types_12.toReaderCall)(field)}(${toNumber}(${place}))`;
          } else if ((0, types_12.isLong)(field) && options.forceLong === options_12.LongOption.BIGINT) {
            return (place) => (0, ts_poet_1.code)`writer.${(0, types_12.toReaderCall)(field)}(${place}.toString())`;
          } else if ((0, types_12.isScalar)(field) || (0, types_12.isEnum)(field)) {
            if ((0, types_12.isLong)(field) && options.forceLong === options_12.LongOption.LONG) {
              return (place) => (0, ts_poet_1.code)`writer.${(0, types_12.toReaderCall)(field)}(${place}.toString())`;
            } else {
              return (place) => (0, ts_poet_1.code)`writer.${(0, types_12.toReaderCall)(field)}(${place})`;
            }
          } else if ((0, types_12.isObjectId)(field) && options.useMongoObjectId) {
            const type2 = (0, types_12.basicTypeName)(ctx2, field, { keepValueType: true });
            return (place) => (0, ts_poet_1.code)`${type2}.encode(${utils.toProtoObjectId}(${place}), writer.fork()).join()`;
          } else if ((0, types_12.isTimestamp)(field) && (options.useDate === options_12.DateOption.DATE || options.useDate === options_12.DateOption.STRING || options.useDate === options_12.DateOption.STRING_NANO || options.useDate === options_12.DateOption.TEMPORAL)) {
            const type2 = (0, types_12.basicTypeName)(ctx2, field, { keepValueType: true });
            return (place) => (0, ts_poet_1.code)`${type2}.encode(${utils.toTimestamp}(${place}), writer.fork()).join()`;
          } else if ((0, types_12.isValueType)(ctx2, field)) {
            const maybeTypeField = (0, options_12.addTypeToMessages)(options) ? `$type: '${field.typeName.slice(1)}',` : "";
            const type2 = (0, types_12.basicTypeName)(ctx2, field, { keepValueType: true });
            const wrappedValue = (place) => {
              if ((0, types_12.isAnyValueType)(field) || (0, types_12.isListValueType)(field) || (0, types_12.isStructType)(field) || (0, types_12.isFieldMaskType)(field)) {
                return (0, ts_poet_1.code)`${type2}.wrap(${place})`;
              }
              return (0, ts_poet_1.code)`{${maybeTypeField} value: ${place}!}`;
            };
            return (place) => (0, ts_poet_1.code)`${type2}.encode(${wrappedValue(place)}, writer.fork()).join()`;
          } else if ((0, types_12.isMessage)(field)) {
            const type2 = (0, types_12.basicTypeName)(ctx2, field);
            if (field.type == ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_GROUP) {
              const endTag = (field.number << 3 | 4) >>> 0;
              return (place) => (0, ts_poet_1.code)`${type2}.encode(${place}, writer).uint32(${endTag})`;
            }
            return (place) => (0, ts_poet_1.code)`${type2}.encode(${place}, writer.fork()).join()`;
          } else {
            throw new Error(`Unhandled field ${field}`);
          }
        };
        chunks.push((0, ts_poet_1.code)`
      encode: (value: ${type}): Uint8Array[] => {
        const encoded: Uint8Array[] = [];
    `);
        const writeSnippet = getEncodeSnippet(ctx, extension);
        if ((0, types_12.isRepeated)(extension)) {
          if (!packed) {
            chunks.push((0, ts_poet_1.code)`
          for (const v of value) {
            const writer = new ${BinaryWriter}();
            ${writeSnippet("v")};
            encoded.push(writer.finish());
          }
        `);
          } else {
            const rhs = (x) => (0, types_12.isLong)(extension) && ctx.options.forceLong === options_12.LongOption.LONG ? `${x}.toString()` : x;
            chunks.push((0, ts_poet_1.code)`
          const writer = new ${BinaryWriter};
          writer.fork();
          for (const v of value) {
            ${writeSnippet(rhs("v"))};
          }
          writer.join();
          encoded.push(writer.finish());
        `);
          }
        } else if ((0, types_12.isScalar)(extension) || (0, types_12.isEnum)(extension)) {
          chunks.push((0, ts_poet_1.code)`
        if (${(0, types_12.notDefaultCheck)(ctx, extension, message?.options, "value")}) {
          const writer = new ${BinaryWriter};
          ${writeSnippet("value")};
          encoded.push(writer.finish());
        }
      `);
        } else {
          chunks.push((0, ts_poet_1.code)`
        const writer = new ${BinaryWriter};
        ${writeSnippet("value")};
        encoded.push(writer.finish());
      `);
        }
        chunks.push((0, ts_poet_1.code)`
        return encoded;
      },
    `);
      }
      if (ctx.options.outputEncodeMethods === true || ctx.options.outputEncodeMethods === "decode-only") {
        chunks.push((0, ts_poet_1.code)`decode: (tag: number, input: Uint8Array[]): ${type} => {`);
        const readSnippet = getDecodeReadSnippet(ctx, extension);
        if ((0, types_12.isRepeated)(extension)) {
          chunks.push((0, ts_poet_1.code)`const values: ${type} = [];`);
          chunks.push((0, ts_poet_1.code)`
        for (const buffer of input) {
          const reader = new ${BinaryReader}(buffer);
      `);
          if (packedTag === void 0) {
            chunks.push((0, ts_poet_1.code)`
          values.push(${readSnippet});
        `);
          } else {
            chunks.push((0, ts_poet_1.code)`
          if (tag == ${packedTag}) {
            const end2 = reader.uint32() + reader.pos;
            while (reader.pos < end2) {
              values.push(${readSnippet});
            }
          } else {
            values.push(${readSnippet});
          }
        `);
          }
          chunks.push((0, ts_poet_1.code)`
          }

          return values;
        },
      `);
        } else {
          chunks.push((0, ts_poet_1.code)`
          const reader = new ${BinaryReader}(input[input.length -1] ?? ${ctx.utils.fail}());
          return ${readSnippet};
        },
      `);
        }
      }
      chunks.push((0, ts_poet_1.code)`}`);
      return {
        name: (0, case_1.maybeSnakeToCamel)(extension.name, ctx.options),
        type,
        extensionInfo: (0, ts_poet_1.joinCode)(chunks, { on: "\n" })
      };
    }
    function generateFromJson(ctx, fullName, fullTypeName, messageDesc) {
      const { options, utils, currentFile } = ctx;
      const chunks = [];
      if (fullTypeName === "google.protobuf.Duration" && ctx.options.useDuration === options_12.DurationOption.STRING) {
        let parseSeconds;
        switch (ctx.options.forceLong) {
          case options_12.LongOption.BIGINT:
            parseSeconds = (0, ts_poet_1.code)`negative && secondsStr !== "0" ? -BigInt(secondsStr) : BigInt(secondsStr)`;
            break;
          case options_12.LongOption.LONG:
            parseSeconds = (0, ts_poet_1.code)`${utils.Long}.fromString(negative ? "-" + secondsStr : secondsStr)`;
            break;
          case options_12.LongOption.STRING:
            parseSeconds = (0, ts_poet_1.code)`negative && secondsStr !== "0" ? "-" + secondsStr : secondsStr`;
            break;
          case options_12.LongOption.NUMBER:
          default:
            parseSeconds = (0, ts_poet_1.code)`negative && secondsStr !== "0" ? -Number(secondsStr) : Number(secondsStr)`;
            break;
        }
        return (0, ts_poet_1.code)`
      fromJSON(object: string): ${fullName} {
        if (!object.endsWith("s")) throw new Error("Invalid duration string");
        const body = object.slice(0, -1);
        const negative = body.startsWith("-");
        const unsigned = negative ? body.slice(1) : body;
        const dot = unsigned.indexOf(".");
        const secondsStr = dot < 0 ? unsigned : unsigned.slice(0, dot);
        const fracStr = dot < 0 ? "" : unsigned.slice(dot + 1);
        if (secondsStr.length === 0) throw new Error("Invalid duration string");
        const seconds = ${parseSeconds};
        const nanosAbs = fracStr === "" ? 0 : Number(fracStr.padEnd(9, "0").slice(0, 9));
        const nanos = negative && nanosAbs !== 0 ? -nanosAbs : nanosAbs;
        return { seconds, nanos };
      }
    `;
      }
      chunks.push((0, ts_poet_1.code)`
    fromJSON(${messageDesc.field.length > 0 ? "object" : "_"}: any): ${fullName} {
      return {
  `);
      if ((0, options_12.addTypeToMessages)(options)) {
        chunks.push((0, ts_poet_1.code)`$type: ${fullName}.$type,`);
      }
      const oneofFieldsCases = messageDesc.oneofDecl.map((oneof, oneofIndex) => messageDesc.field.filter(types_12.isWithinOneOf).filter((field) => field.oneofIndex === oneofIndex));
      const canonicalFromJson = {
        ["google.protobuf.FieldMask"]: {
          paths: (from) => (0, ts_poet_1.code)`typeof(${from}) === 'string'
        ? ${from}.split(",").filter(${ctx.utils.globalThis}.Boolean)
        : ${ctx.utils.globalThis}.Array.isArray(${from}?.paths)
        ? ${from}.paths.map(${ctx.utils.globalThis}.String)
        : []`
        }
      };
      messageDesc.field.forEach((field) => {
        const fieldName = (0, utils_12.getFieldName)(field, options);
        const fieldKey = (0, utils_12.safeAccessor)(fieldName);
        const jsonName = (0, utils_12.getFieldJsonName)(field, options);
        const jsonProperty = (0, utils_12.getPropertyAccessor)("object", jsonName);
        const jsonPropertyOptional = (0, utils_12.getPropertyAccessor)("object", jsonName, true);
        const readSnippet = (from) => {
          if ((0, types_12.isEnum)(field)) {
            const fromJson = (0, types_12.getEnumMethod)(ctx, field.typeName, "FromJSON");
            return (0, ts_poet_1.code)`${fromJson}(${from})`;
          } else if ((0, types_12.isPrimitive)(field)) {
            if ((0, types_12.isBytes)(field)) {
              if (options.env === options_12.EnvOption.NODE) {
                return (0, ts_poet_1.code)`Buffer.from(${utils.bytesFromBase64}(${from}))`;
              } else {
                return (0, ts_poet_1.code)`${utils.bytesFromBase64}(${from})`;
              }
            } else if ((0, types_12.isLong)(field) && (0, types_12.isJsTypeFieldOption)(options, field)) {
              const fieldType = (0, types_12.getFieldOptionsJsType)(field, ctx.options) ?? field.type;
              const cstr = (0, case_1.capitalize)((0, types_12.basicTypeName)(ctx, { ...field, type: fieldType }, { keepValueType: true }).toCodeString([]));
              return (0, ts_poet_1.code)`${utils.globalThis}.${cstr}(${from})`;
            } else if ((0, types_12.isLong)(field) && options.forceLong === options_12.LongOption.LONG) {
              const cstr = (0, case_1.capitalize)((0, types_12.basicTypeName)(ctx, field, { keepValueType: true }).toCodeString([]));
              return (0, ts_poet_1.code)`${cstr}.fromValue(${from})`;
            } else if ((0, types_12.isLong)(field) && options.forceLong === options_12.LongOption.BIGINT) {
              return (0, ts_poet_1.code)`BigInt(${from})`;
            } else {
              const cstr = (0, case_1.capitalize)((0, types_12.basicTypeName)(ctx, field, { keepValueType: true }).toCodeString([]));
              return (0, ts_poet_1.code)`${utils.globalThis}.${cstr}(${from})`;
            }
          } else if ((0, types_12.isObjectId)(field) && options.useMongoObjectId) {
            return (0, ts_poet_1.code)`${utils.fromJsonObjectId}(${from})`;
          } else if ((0, types_12.isTimestamp)(field) && (options.useDate === options_12.DateOption.STRING || options.useDate === options_12.DateOption.STRING_NANO)) {
            return (0, ts_poet_1.code)`${utils.globalThis}.String(${from})`;
          } else if ((0, types_12.isTimestamp)(field) && (options.useDate === options_12.DateOption.DATE || options.useDate === options_12.DateOption.TEMPORAL || options.useDate === options_12.DateOption.TIMESTAMP)) {
            return (0, ts_poet_1.code)`${utils.fromJsonTimestamp}(${from})`;
          } else if ((0, types_12.isAnyValueType)(field) || (0, types_12.isStructType)(field)) {
            return (0, ts_poet_1.code)`${from}`;
          } else if ((0, types_12.isFieldMaskType)(field)) {
            const type = (0, types_12.basicTypeName)(ctx, field, { keepValueType: true });
            return (0, ts_poet_1.code)`${type}.unwrap(${type}.fromJSON(${from}))`;
          } else if ((0, types_12.isListValueType)(field)) {
            return (0, ts_poet_1.code)`[...${from}]`;
          } else if ((0, types_12.isValueType)(ctx, field)) {
            const valueType = (0, types_12.valueTypeName)(ctx, field.typeName);
            if ((0, types_12.isLongValueType)(field) && options.forceLong === options_12.LongOption.LONG) {
              return (0, ts_poet_1.code)`${(0, case_1.capitalize)(valueType.toCodeString([]))}.fromValue(${from})`;
            } else if ((0, types_12.isLongValueType)(field) && options.forceLong === options_12.LongOption.BIGINT) {
              return (0, ts_poet_1.code)`BigInt(${from})`;
            } else if ((0, types_12.isBytesValueType)(field)) {
              return (0, ts_poet_1.code)`new ${(0, case_1.capitalize)(valueType.toCodeString([]))}(${from})`;
            } else {
              return (0, ts_poet_1.code)`${(0, case_1.capitalize)(valueType.toCodeString([]))}(${from})`;
            }
          } else if ((0, types_12.isMessage)(field)) {
            if ((0, types_12.isRepeated)(field) && (0, types_12.isMapType)(ctx, messageDesc, field)) {
              const { valueField, valueType } = (0, types_12.detectMapType)(ctx, messageDesc, field);
              if ((0, types_12.isPrimitive)(valueField)) {
                if ((0, types_12.isBytes)(valueField)) {
                  if (options.env === options_12.EnvOption.NODE) {
                    return (0, ts_poet_1.code)`Buffer.from(${utils.bytesFromBase64}(${from} as string))`;
                  } else {
                    return (0, ts_poet_1.code)`${utils.bytesFromBase64}(${from} as string)`;
                  }
                } else if ((0, types_12.isLong)(valueField) && options.forceLong === options_12.LongOption.LONG) {
                  return (0, ts_poet_1.code)`Long.fromValue(${from} as Long | string)`;
                } else if ((0, types_12.isLong)(valueField) && options.forceLong === options_12.LongOption.BIGINT) {
                  return (0, ts_poet_1.code)`BigInt(${from} as string | number | bigint | boolean)`;
                } else if ((0, types_12.isEnum)(valueField)) {
                  const fromJson = (0, types_12.getEnumMethod)(ctx, valueField.typeName, "FromJSON");
                  return (0, ts_poet_1.code)`${fromJson}(${from})`;
                } else {
                  const cstr = (0, case_1.capitalize)(valueType.toCodeString([]));
                  return (0, ts_poet_1.code)`${utils.globalThis}.${cstr}(${from})`;
                }
              } else if ((0, types_12.isObjectId)(valueField) && options.useMongoObjectId) {
                return (0, ts_poet_1.code)`${utils.fromJsonObjectId}(${from})`;
              } else if ((0, types_12.isTimestamp)(valueField) && (options.useDate === options_12.DateOption.STRING || options.useDate === options_12.DateOption.STRING_NANO)) {
                return (0, ts_poet_1.code)`${utils.globalThis}.String(${from})`;
              } else if ((0, types_12.isTimestamp)(valueField) && (options.useDate === options_12.DateOption.DATE || options.useDate === options_12.DateOption.TEMPORAL || options.useDate === options_12.DateOption.TIMESTAMP)) {
                return (0, ts_poet_1.code)`${utils.fromJsonTimestamp}(${from})`;
              } else if ((0, types_12.isValueType)(ctx, valueField)) {
                return (0, ts_poet_1.code)`${from} as ${valueType}`;
              } else if ((0, types_12.isAnyValueType)(valueField)) {
                return (0, ts_poet_1.code)`${from}`;
              } else {
                return (0, ts_poet_1.code)`${valueType}.fromJSON(${from})`;
              }
            } else {
              const type = (0, types_12.basicTypeName)(ctx, field);
              return (0, ts_poet_1.code)`${type}.fromJSON(${from})`;
            }
          } else {
            throw new Error(`Unhandled field ${field}`);
          }
        };
        const noDefaultValue = !options.initializeFieldsAsUndefined && (0, types_12.isOptionalProperty)(field, messageDesc.options, options, currentFile.isProto3Syntax);
        let protoJsonComparison = (0, ts_poet_1.code)``;
        if (canonicalFromJson[fullTypeName]?.[fieldName]) {
          chunks.push((0, ts_poet_1.code)`${fieldName}: ${canonicalFromJson[fullTypeName][fieldName]("object")},`);
        } else if ((0, types_12.isRepeated)(field)) {
          if ((0, types_12.isMapType)(ctx, messageDesc, field)) {
            const mapInfo = (0, types_12.detectMapType)(ctx, messageDesc, field);
            const fieldType = (0, types_12.toTypeName)(ctx, messageDesc, field);
            const i = convertFromObjectKey(ctx, messageDesc, field, "key");
            if ((0, types_12.shouldGenerateJSMapType)(ctx, messageDesc, field)) {
              const fallback = noDefaultValue ? (0, utils_12.nullOrUndefined)(options) : "new Map()";
              if (options.protoJsonFormat && field.name !== field.jsonName) {
                const protoJsonProperty = (0, utils_12.getPropertyAccessor)("object", field.name);
                protoJsonComparison = (0, ts_poet_1.code)`
                : ${ctx.utils.isObject}(${protoJsonProperty})
                ? (${ctx.utils.globalThis}.Object.entries(${protoJsonProperty}) as [string, any][]).reduce((acc: ${fieldType}, [key, value]: [string, any]) => {
                    acc.set(${i}, ${readSnippet("value")});
                    return acc;
                  }, new Map())
              `;
              }
              chunks.push((0, ts_poet_1.code)`
            ${fieldKey}: ${ctx.utils.isObject}(${jsonProperty})
              ? (${ctx.utils.globalThis}.Object.entries(${jsonProperty}) as [string, any][]).reduce((acc: ${fieldType}, [key, value]: [string, any]) => {
                  acc.set(${i}, ${readSnippet("value")});
                  return acc;
                }, new Map())
              ${protoJsonComparison}
              : ${fallback},
          `);
            } else {
              const fallback = noDefaultValue ? (0, utils_12.nullOrUndefined)(options) : "{}";
              if (options.protoJsonFormat && field.name !== field.jsonName) {
                const protoJsonProperty = (0, utils_12.getPropertyAccessor)("object", field.name);
                protoJsonComparison = (0, ts_poet_1.code)`
                 : ${ctx.utils.isObject}(${protoJsonProperty})
                 ? (${ctx.utils.globalThis}.Object.entries(${protoJsonProperty}) as [string, any][]).reduce((acc: ${fieldType}, [key, value]: [string, any]) => {
                     ${ctx.utils.globalThis}.Object.defineProperty(acc, ${i}, {
                       value: ${readSnippet("value")},
                       enumerable: true, configurable: true, writable: true,
                     });
                     return acc;
                   }, {})
               `;
              }
              chunks.push((0, ts_poet_1.code)`
            ${fieldKey}: ${ctx.utils.isObject}(${jsonProperty})
              ? (${ctx.utils.globalThis}.Object.entries(${jsonProperty}) as [string, any][]).reduce((acc: ${fieldType}, [key, value]: [string, any]) => {
                  ${ctx.utils.globalThis}.Object.defineProperty(acc, ${i}, {
                    value: ${readSnippet("value")},
                    enumerable: true, configurable: true, writable: true,
                  });
                  return acc;
                }, {})
              ${protoJsonComparison}
              : ${fallback},
          `);
            }
          } else {
            const fallback = noDefaultValue ? (0, utils_12.nullOrUndefined)(options) : "[]";
            const needMap = readSnippet("e").toString() !== (0, ts_poet_1.code)`e`.toString();
            if (!needMap) {
              if (options.protoJsonFormat && field.name !== field.jsonName) {
                const protoJsonProperty = (0, utils_12.getPropertyAccessor)("object", field.name);
                const protoJsonPropertyOptional = (0, utils_12.getPropertyAccessor)("object", field.name, true);
                protoJsonComparison = (0, ts_poet_1.code)` : ${ctx.utils.globalThis}.Array.isArray(${protoJsonPropertyOptional}) ? [...${protoJsonProperty}]`;
              }
              chunks.push((0, ts_poet_1.code)`${fieldKey}: ${ctx.utils.globalThis}.Array.isArray(${jsonPropertyOptional}) ? [...${jsonProperty}] ${protoJsonComparison} : [],`);
            } else {
              if (options.protoJsonFormat && field.name !== field.jsonName) {
                const protoJsonProperty = (0, utils_12.getPropertyAccessor)("object", field.name);
                const protoJsonPropertyOptional = (0, utils_12.getPropertyAccessor)("object", field.name, true);
                protoJsonComparison = (0, ts_poet_1.code)` : ${ctx.utils.globalThis}.Array.isArray(${protoJsonPropertyOptional}) ? ${protoJsonProperty}.map((e: any) => ${readSnippet("e")})`;
              }
              chunks.push((0, ts_poet_1.code)`
            ${fieldKey}: ${ctx.utils.globalThis}.Array.isArray(${jsonPropertyOptional}) ? ${jsonProperty}.map((e: any) => ${readSnippet("e")}) ${protoJsonComparison} : ${fallback},
          `);
            }
          }
        } else if ((0, types_12.isWithinOneOfThatShouldBeUnion)(options, field)) {
          const cases = oneofFieldsCases[field.oneofIndex];
          const firstCase = cases[0];
          const lastCase = cases[cases.length - 1];
          if (field === firstCase) {
            const fieldName2 = (0, case_1.maybeSnakeToCamel)(messageDesc.oneofDecl[field.oneofIndex].name, options);
            chunks.push((0, ts_poet_1.code)`${fieldName2}: `);
          }
          const valueName = (0, utils_12.oneofValueName)(fieldKey, options);
          const ternaryIf = (0, ts_poet_1.code)`${ctx.utils.isSet}(${jsonProperty})`;
          const ternaryThen = (0, ts_poet_1.code)`{ $case: '${fieldName}', ${valueName}: ${readSnippet(`${jsonProperty}`)}`;
          chunks.push((0, ts_poet_1.code)`${ternaryIf} ? ${ternaryThen}} : `);
          if (options.protoJsonFormat && field.name !== field.jsonName) {
            const protoJsonProperty = (0, utils_12.getPropertyAccessor)("object", field.name);
            const ternaryOriginalIf = (0, ts_poet_1.code)`${ctx.utils.isSet}(${protoJsonProperty})`;
            const ternaryOriginalThen = (0, ts_poet_1.code)`{ $case: '${fieldName}', ${valueName}: ${readSnippet(`${protoJsonProperty}`)}`;
            chunks.push((0, ts_poet_1.code)`${ternaryOriginalIf} ? ${ternaryOriginalThen}} : `);
          }
          if (field === lastCase) {
            chunks.push((0, ts_poet_1.code)`${(0, utils_12.nullOrUndefined)(options)},`);
          }
        } else if ((0, types_12.isAnyValueType)(field)) {
          if (options.protoJsonFormat && field.name !== field.jsonName) {
            const protoJsonProperty = (0, utils_12.getPropertyAccessor)("object", field.name);
            const protoJsonPropertyOptional = (0, utils_12.getPropertyAccessor)("object", field.name, true);
            protoJsonComparison = (0, ts_poet_1.code)` : ${ctx.utils.isSet}(${protoJsonPropertyOptional}) ? ${readSnippet(`${protoJsonProperty}`)}`;
          }
          chunks.push((0, ts_poet_1.code)`${fieldKey}: ${ctx.utils.isSet}(${jsonPropertyOptional})
        ? ${readSnippet(`${jsonProperty}`)}
        ${protoJsonComparison}
        : ${(0, utils_12.nullOrUndefined)(options)},
      `);
        } else if ((0, types_12.isStructType)(field)) {
          if (options.protoJsonFormat && field.name !== field.jsonName) {
            const protoJsonProperty = (0, utils_12.getPropertyAccessor)("object", field.name);
            protoJsonComparison = (0, ts_poet_1.code)` : ${ctx.utils.isObject}(${protoJsonProperty}) ? ${readSnippet(`${protoJsonProperty}`)}`;
          }
          chunks.push((0, ts_poet_1.code)`${fieldKey}: ${ctx.utils.isObject}(${jsonProperty})
          ? ${readSnippet(`${jsonProperty}`)}
          ${protoJsonComparison}
          : ${(0, utils_12.nullOrUndefined)(options)},`);
        } else if ((0, types_12.isListValueType)(field)) {
          if (options.protoJsonFormat && field.name !== field.jsonName) {
            const protoJsonProperty = (0, utils_12.getPropertyAccessor)("object", field.name);
            protoJsonComparison = (0, ts_poet_1.code)` : ${ctx.utils.globalThis}.Array.isArray(${protoJsonProperty}) ? ${readSnippet(`${protoJsonProperty}`)}`;
          }
          chunks.push((0, ts_poet_1.code)`
        ${fieldKey}: ${ctx.utils.globalThis}.Array.isArray(${jsonProperty})
          ? ${readSnippet(`${jsonProperty}`)}
          ${protoJsonComparison}
          : ${(0, utils_12.nullOrUndefined)(options)},
      `);
        } else {
          const fallback = (0, types_12.isWithinOneOf)(field) || noDefaultValue ? (0, utils_12.nullOrUndefined)(options) : (0, types_12.defaultValue)(ctx, field);
          if (options.protoJsonFormat && field.name !== field.jsonName) {
            const protoJsonProperty = (0, utils_12.getPropertyAccessor)("object", field.name);
            protoJsonComparison = (0, ts_poet_1.code)` : ${ctx.utils.isSet}(${protoJsonProperty}) ? ${readSnippet(`${protoJsonProperty}`)}`;
          }
          chunks.push((0, ts_poet_1.code)`
        ${fieldKey}: ${ctx.utils.isSet}(${jsonProperty}) ? ${readSnippet(`${jsonProperty}`)}
        ${protoJsonComparison}
          : ${fallback},
      `);
        }
      });
      chunks.push((0, ts_poet_1.code)`};`);
      chunks.push((0, ts_poet_1.code)`}`);
      return (0, ts_poet_1.joinCode)(chunks, { on: "\n" });
    }
    function generateCanonicalToJson(fullName, fullProtobufTypeName, { useOptionals, useNullAsOptional, useDuration, forceLong }) {
      if ((0, types_12.isFieldMaskTypeName)(fullProtobufTypeName)) {
        const returnType = useOptionals === "all" ? `string | ${(0, utils_12.nullOrUndefined)({ useNullAsOptional })}` : "string";
        const pathModifier = useOptionals === "all" ? "?" : "";
        return (0, ts_poet_1.code)`
    toJSON(message: ${fullName}): ${returnType} {
      return message.paths${pathModifier}.join(',');
    }
  `;
      }
      if (fullProtobufTypeName === "google.protobuf.Duration" && useDuration === options_12.DurationOption.STRING) {
        let isNegSeconds;
        let absSeconds;
        switch (forceLong) {
          case options_12.LongOption.BIGINT:
            isNegSeconds = (0, ts_poet_1.code)`message.seconds < 0n`;
            absSeconds = (0, ts_poet_1.code)`message.seconds < 0n ? -message.seconds : message.seconds`;
            break;
          case options_12.LongOption.LONG:
            isNegSeconds = (0, ts_poet_1.code)`message.seconds.isNegative()`;
            absSeconds = (0, ts_poet_1.code)`message.seconds.isNegative() ? message.seconds.toString().slice(1) : message.seconds.toString()`;
            break;
          case options_12.LongOption.STRING:
            isNegSeconds = (0, ts_poet_1.code)`message.seconds.startsWith("-")`;
            absSeconds = (0, ts_poet_1.code)`message.seconds.startsWith("-") ? message.seconds.slice(1) : message.seconds`;
            break;
          case options_12.LongOption.NUMBER:
          default:
            isNegSeconds = (0, ts_poet_1.code)`message.seconds < 0`;
            absSeconds = (0, ts_poet_1.code)`message.seconds < 0 ? -message.seconds : message.seconds`;
            break;
        }
        return (0, ts_poet_1.code)`
    toJSON(message: ${fullName}): string {
      const negative = ${isNegSeconds} || message.nanos < 0;
      const sign = negative ? "-" : "";
      const absSeconds = ${absSeconds};
      const absNanos = message.nanos < 0 ? -message.nanos : message.nanos;
      if (absNanos === 0) return \`\${sign}\${absSeconds}s\`;
      const padded = absNanos.toString().padStart(9, "0");
      const len = absNanos % 1_000_000 === 0 ? 3 : absNanos % 1_000 === 0 ? 6 : 9;
      return \`\${sign}\${absSeconds}.\${padded.slice(0, len)}s\`;
    }
    `;
      }
      return void 0;
    }
    function generateToJson(ctx, fullName, fullProtobufTypeName, messageDesc) {
      const { options, utils, typeMap } = ctx;
      const chunks = [];
      const canonicalToJson = generateCanonicalToJson(fullName, fullProtobufTypeName, options);
      if (canonicalToJson) {
        chunks.push(canonicalToJson);
        return (0, ts_poet_1.joinCode)(chunks, { on: "\n" });
      }
      chunks.push((0, ts_poet_1.code)`
    toJSON(${messageDesc.field.length > 0 ? "message" : "_"}: ${fullName}): unknown {
      const obj: any = {};
  `);
      let currentIfTarget = "";
      messageDesc.field.forEach((field) => {
        const fieldName = (0, utils_12.getFieldName)(field, options);
        const jsonName = (0, utils_12.getFieldJsonName)(field, options);
        const jsonProperty = (0, utils_12.getPropertyAccessor)("obj", jsonName);
        const messageProperty = (0, utils_12.getPropertyAccessor)("message", fieldName);
        const setJsonProperty = (value) => jsonName === "__proto__" ? (0, ts_poet_1.code)`${utils.globalThis}.Object.defineProperty(obj, "__proto__", { value: ${value}, enumerable: true });` : (0, ts_poet_1.code)`${jsonProperty} = ${value};`;
        const readSnippet = (from) => {
          if ((0, types_12.isEnum)(field)) {
            const toJson = (0, types_12.getEnumMethod)(ctx, field.typeName, "ToJSON");
            return (0, ts_poet_1.code)`${toJson}(${from})`;
          } else if ((0, types_12.isObjectId)(field) && options.useMongoObjectId) {
            return (0, ts_poet_1.code)`${from}.toString()`;
          } else if ((0, types_12.isTimestamp)(field) && options.useDate === options_12.DateOption.DATE) {
            return (0, ts_poet_1.code)`${from}.toISOString()`;
          } else if ((0, types_12.isTimestamp)(field) && options.useDate === options_12.DateOption.TEMPORAL) {
            return (0, ts_poet_1.code)`${from}.toString()`;
          } else if ((0, types_12.isTimestamp)(field) && (options.useDate === options_12.DateOption.STRING || options.useDate === options_12.DateOption.STRING_NANO)) {
            return (0, ts_poet_1.code)`${from}`;
          } else if ((0, types_12.isTimestamp)(field) && options.useDate === options_12.DateOption.TIMESTAMP) {
            if (options.useJsonTimestamp === options_12.JsonTimestampOption.RAW) {
              return (0, ts_poet_1.code)`${from}`;
            }
            return (0, ts_poet_1.code)`${utils.fromTimestamp}(${from}).toISOString()`;
          } else if ((0, types_12.isMapType)(ctx, messageDesc, field)) {
            const valueType = typeMap.get(field.typeName)[2].field[1];
            if ((0, types_12.isEnum)(valueType)) {
              const toJson = (0, types_12.getEnumMethod)(ctx, valueType.typeName, "ToJSON");
              return (0, ts_poet_1.code)`${toJson}(${from})`;
            } else if ((0, types_12.isBytes)(valueType)) {
              return (0, ts_poet_1.code)`${utils.base64FromBytes}(${from})`;
            } else if ((0, types_12.isObjectId)(valueType) && options.useMongoObjectId) {
              return (0, ts_poet_1.code)`${from}.toString()`;
            } else if ((0, types_12.isTimestamp)(valueType) && options.useDate === options_12.DateOption.DATE) {
              return (0, ts_poet_1.code)`${from}.toISOString()`;
            } else if ((0, types_12.isTimestamp)(valueType) && options.useDate === options_12.DateOption.TEMPORAL) {
              return (0, ts_poet_1.code)`${from}.toString()`;
            } else if ((0, types_12.isTimestamp)(valueType) && (options.useDate === options_12.DateOption.STRING || options.useDate === options_12.DateOption.STRING_NANO)) {
              return (0, ts_poet_1.code)`${from}`;
            } else if ((0, types_12.isTimestamp)(valueType) && options.useDate === options_12.DateOption.TIMESTAMP) {
              return (0, ts_poet_1.code)`${utils.fromTimestamp}(${from}).toISOString()`;
            } else if ((0, types_12.isLong)(valueType) && options.forceLong === options_12.LongOption.LONG) {
              return (0, ts_poet_1.code)`${from}.toString()`;
            } else if ((0, types_12.isLong)(valueType) && options.forceLong === options_12.LongOption.BIGINT) {
              return (0, ts_poet_1.code)`${from}.toString()`;
            } else if ((0, types_12.isWholeNumber)(valueType) && !((0, types_12.isLong)(valueType) && options.forceLong === options_12.LongOption.STRING)) {
              return (0, ts_poet_1.code)`Math.round(${from})`;
            } else if ((0, types_12.isScalar)(valueType) || (0, types_12.isValueType)(ctx, valueType)) {
              return (0, ts_poet_1.code)`${from}`;
            } else if ((0, types_12.isAnyValueType)(valueType)) {
              return (0, ts_poet_1.code)`${from}`;
            } else {
              const type = (0, types_12.basicTypeName)(ctx, valueType);
              return (0, ts_poet_1.code)`${type}.toJSON(${from})`;
            }
          } else if ((0, types_12.isAnyValueType)(field)) {
            return (0, ts_poet_1.code)`${from}`;
          } else if ((0, types_12.isFieldMaskType)(field)) {
            const type = (0, types_12.basicTypeName)(ctx, field, { keepValueType: true });
            return (0, ts_poet_1.code)`${type}.toJSON(${type}.wrap(${from}))`;
          } else if ((0, types_12.isMessage)(field) && !(0, types_12.isValueType)(ctx, field) && !(0, types_12.isMapType)(ctx, messageDesc, field)) {
            const type = (0, types_12.basicTypeName)(ctx, field, { keepValueType: true });
            return (0, ts_poet_1.code)`${type}.toJSON(${from})`;
          } else if ((0, types_12.isBytes)(field)) {
            return (0, ts_poet_1.code)`${utils.base64FromBytes}(${from})`;
          } else if ((0, types_12.isLong)(field) && (0, types_12.isJsTypeFieldOption)(options, field)) {
            const fieldType = (0, types_12.getFieldOptionsJsType)(field, ctx.options) ?? field.type;
            if (!fieldType) {
              return (0, ts_poet_1.code)`${from}`;
            }
            const cstr = (0, case_1.capitalize)((0, types_12.basicTypeName)(ctx, { ...field, type: fieldType }, { keepValueType: true }).toCodeString([]));
            return (0, ts_poet_1.code)`${utils.globalThis}.${cstr}(${from})`;
          } else if ((0, types_12.isLong)(field) && options.forceLong === options_12.LongOption.LONG) {
            return (0, ts_poet_1.code)`(${from} || ${(0, types_12.defaultValue)(ctx, field)}).toString()`;
          } else if ((0, types_12.isLong)(field) && options.forceLong === options_12.LongOption.BIGINT) {
            return (0, ts_poet_1.code)`${from}.toString()`;
          } else if ((0, types_12.isWholeNumber)(field) && !((0, types_12.isLong)(field) && options.forceLong === options_12.LongOption.STRING)) {
            return (0, ts_poet_1.code)`Math.round(${from})`;
          } else {
            return (0, ts_poet_1.code)`${from}`;
          }
        };
        if ((0, types_12.isMapType)(ctx, messageDesc, field)) {
          const i = convertToObjectKey(ctx, messageDesc, field, "k");
          if ((0, types_12.shouldGenerateJSMapType)(ctx, messageDesc, field)) {
            chunks.push((0, ts_poet_1.code)`
          if (${messageProperty}?.size) {
            ${setJsonProperty("{}")}
            ${messageProperty}.forEach((v, k) => {
              ${jsonProperty}[${i}] = ${readSnippet("v")};
            });
          }
        `);
          } else {
            const mapInfo = (0, types_12.detectMapType)(ctx, messageDesc, field);
            chunks.push((0, ts_poet_1.code)`
        if (${messageProperty}) {
            const entries = ${utils.globalThis}.Object.entries(${messageProperty}) as [string, ${mapInfo.valueType}][];
            if (entries.length > 0) {
              ${setJsonProperty("{}")}
              entries.forEach(([k, v]) => {
                ${jsonProperty}[${i}] = ${readSnippet("v")};
              });
            }
          }
        `);
          }
        } else if ((0, types_12.isRepeated)(field)) {
          const needMap = readSnippet("e").toCodeString([]) !== "e";
          const maybeMap = needMap ? (0, ts_poet_1.code)`.map(e => ${readSnippet("e")})` : "";
          chunks.push((0, ts_poet_1.code)`
        if (${messageProperty}?.length) {
          ${setJsonProperty((0, ts_poet_1.code)`${messageProperty}${maybeMap}`)}
        }
      `);
        } else if ((0, types_12.isWithinOneOfThatShouldBeUnion)(options, field)) {
          const oneofNameWithMessage = options.useJsonName ? messageProperty : (0, utils_12.getPropertyAccessor)("message", (0, case_1.maybeSnakeToCamel)(messageDesc.oneofDecl[field.oneofIndex].name, options));
          const valueName = (0, utils_12.oneofValueName)(fieldName, options);
          chunks.push((0, ts_poet_1.code)`
        ${currentIfTarget === oneofNameWithMessage ? "else " : ""}if (${oneofNameWithMessage}?.$case === '${fieldName}') {
          ${setJsonProperty(readSnippet(`${oneofNameWithMessage}.${valueName}`))}
        }
      `);
          currentIfTarget = oneofNameWithMessage;
        } else {
          let emitDefaultValuesForJson = ctx.options.emitDefaultValues.includes("json-methods");
          const check = ((0, types_12.isScalar)(field) || (0, types_12.isEnum)(field)) && !((0, types_12.isWithinOneOf)(field) || emitDefaultValuesForJson) ? (0, types_12.notDefaultCheck)(ctx, field, messageDesc.options, `${messageProperty}`) : `${messageProperty} !== undefined ${(0, utils_12.withAndMaybeCheckIsNotNull)(options, messageProperty)}`;
          chunks.push((0, ts_poet_1.code)`
        if (${check}) {
          ${setJsonProperty(readSnippet(`${messageProperty}`))}
        }
      `);
        }
      });
      chunks.push((0, ts_poet_1.code)`return obj;`);
      chunks.push((0, ts_poet_1.code)`}`);
      return (0, ts_poet_1.joinCode)(chunks, { on: "\n" });
    }
    function generateFromPartial(ctx, fullName, messageDesc) {
      const { options, utils } = ctx;
      const chunks = [];
      if (ctx.options.useExactTypes) {
        chunks.push((0, ts_poet_1.code)`
      create<I extends ${utils.Exact}<${utils.DeepPartial}<${fullName}>, I>>(base?: I): ${fullName} {
        return ${fullName}.fromPartial(base ?? ({} as any));
      },
    `);
      } else {
        chunks.push((0, ts_poet_1.code)`
      create(base?: ${utils.DeepPartial}<${fullName}>): ${fullName} {
        return ${fullName}.fromPartial(base ?? {});
      },
    `);
      }
      const paramName = messageDesc.field.length > 0 ? "object" : "_";
      if (ctx.options.useExactTypes) {
        chunks.push((0, ts_poet_1.code)`
      fromPartial<I extends ${utils.Exact}<${utils.DeepPartial}<${fullName}>, I>>(${paramName}: I): ${fullName} {
    `);
      } else {
        chunks.push((0, ts_poet_1.code)`
      fromPartial(${paramName}: ${utils.DeepPartial}<${fullName}>): ${fullName} {
    `);
      }
      let createBase = (0, ts_poet_1.code)`createBase${fullName}()`;
      if (options.usePrototypeForDefaults) {
        createBase = (0, ts_poet_1.code)`Object.create(${createBase}) as ${fullName}`;
      }
      chunks.push((0, ts_poet_1.code)`const message = ${createBase}${maybeAsAny(options)};`);
      let currentSwitchTarget;
      messageDesc.field.forEach((field) => {
        const fieldName = (0, utils_12.getFieldName)(field, options);
        const messageProperty = (0, utils_12.getPropertyAccessor)("message", fieldName);
        const objectProperty = (0, utils_12.getPropertyAccessor)("object", fieldName);
        if (currentSwitchTarget && !(0, types_12.isWithinOneOfThatShouldBeUnion)(options, field)) {
          chunks.push((0, ts_poet_1.code)`}`);
          currentSwitchTarget = void 0;
        }
        const readSnippet = (from) => {
          if (((0, types_12.isLong)(field) || (0, types_12.isLongValueType)(field)) && options.forceLong === options_12.LongOption.LONG && !(0, types_12.isJsTypeFieldOption)(options, field)) {
            return (0, ts_poet_1.code)`Long.fromValue(${from})`;
          } else if (((0, types_12.isLong)(field) || (0, types_12.isLongValueType)(field)) && options.forceLong === options_12.LongOption.BIGINT && !(0, types_12.isJsTypeFieldOption)(options, field)) {
            return (0, ts_poet_1.code)`BigInt(${from})`;
          } else if ((0, types_12.isObjectId)(field) && options.useMongoObjectId) {
            return (0, ts_poet_1.code)`${from} as mongodb.ObjectId`;
          } else if ((0, types_12.isPrimitive)(field) || (0, types_12.isTimestamp)(field) && (options.useDate === options_12.DateOption.DATE || options.useDate === options_12.DateOption.STRING || options.useDate === options_12.DateOption.STRING_NANO || options.useDate === options_12.DateOption.TEMPORAL) || (0, types_12.isValueType)(ctx, field)) {
            return (0, ts_poet_1.code)`${from}`;
          } else if ((0, types_12.isMessage)(field)) {
            if ((0, types_12.isRepeated)(field) && (0, types_12.isMapType)(ctx, messageDesc, field)) {
              const { valueField, valueType } = (0, types_12.detectMapType)(ctx, messageDesc, field);
              if ((0, types_12.isPrimitive)(valueField)) {
                if ((0, types_12.isBytes)(valueField)) {
                  return (0, ts_poet_1.code)`${from}`;
                } else if ((0, types_12.isEnum)(valueField)) {
                  return (0, ts_poet_1.code)`${from} as ${valueType}`;
                } else if ((0, types_12.isLong)(valueField) && options.forceLong === options_12.LongOption.LONG) {
                  return (0, ts_poet_1.code)`Long.fromValue(${from})`;
                } else if ((0, types_12.isLong)(valueField) && options.forceLong === options_12.LongOption.BIGINT) {
                  return (0, ts_poet_1.code)`BigInt(${from} as string | number | bigint | boolean)`;
                } else {
                  const cstr = (0, case_1.capitalize)(valueType.toCodeString([]));
                  return (0, ts_poet_1.code)`${utils.globalThis}.${cstr}(${from})`;
                }
              } else if ((0, types_12.isAnyValueType)(valueField)) {
                return (0, ts_poet_1.code)`${from}`;
              } else if ((0, types_12.isObjectId)(valueField) && options.useMongoObjectId) {
                return (0, ts_poet_1.code)`${from} as mongodb.ObjectId`;
              } else if ((0, types_12.isTimestamp)(valueField) && (options.useDate === options_12.DateOption.DATE || options.useDate === options_12.DateOption.STRING || options.useDate === options_12.DateOption.STRING_NANO || options.useDate === options_12.DateOption.TEMPORAL)) {
                return (0, ts_poet_1.code)`${from}`;
              } else if ((0, types_12.isValueType)(ctx, valueField)) {
                return (0, ts_poet_1.code)`${from}`;
              } else {
                const type = (0, types_12.basicTypeName)(ctx, valueField);
                return (0, ts_poet_1.code)`${type}.fromPartial(${from})`;
              }
            } else if ((0, types_12.isAnyValueType)(field)) {
              return (0, ts_poet_1.code)`${from}`;
            } else {
              const type = (0, types_12.basicTypeName)(ctx, field);
              return (0, ts_poet_1.code)`${type}.fromPartial(${from})`;
            }
          } else {
            throw new Error(`Unhandled field ${field}`);
          }
        };
        const noDefaultValue = !options.initializeFieldsAsUndefined && (0, types_12.isOptionalProperty)(field, messageDesc.options, options, true);
        if ((0, types_12.isRepeated)(field)) {
          if ((0, types_12.isMapType)(ctx, messageDesc, field)) {
            const mapInfo = (0, types_12.detectMapType)(ctx, messageDesc, field);
            const fieldType = (0, types_12.toTypeName)(ctx, messageDesc, field);
            const i = convertFromObjectKey(ctx, messageDesc, field, "key");
            const noValueSnippet = noDefaultValue ? `(${objectProperty} === undefined || ${objectProperty} === null) ? undefined : ` : "";
            if ((0, types_12.shouldGenerateJSMapType)(ctx, messageDesc, field)) {
              chunks.push((0, ts_poet_1.code)`
            ${messageProperty} = ${noValueSnippet} (() => {
              const m = new Map();
              (${objectProperty} as ${fieldType} ?? new Map()).forEach((value, key) => {
                if (value !== undefined) {
                  m.set(key, ${readSnippet("value")});
                }
              });
              return m;
            })();
          `);
            } else {
              chunks.push((0, ts_poet_1.code)`
            ${messageProperty} = ${noValueSnippet} (${utils.globalThis}.Object.entries(${objectProperty} ?? {}) as [string, ${mapInfo.valueType}][]).reduce(
              (acc: ${fieldType}, [key, value]: [string, ${mapInfo.valueType}]) => {
                if (value !== undefined) {
                  acc[${i}] = ${readSnippet("value")};
                }
                return acc;
              },
              {},
            );
          `);
            }
          } else {
            const fallback = noDefaultValue ? "undefined" : "[]";
            chunks.push((0, ts_poet_1.code)`
          ${messageProperty} = ${objectProperty}?.map((e) => ${readSnippet("e")}) || ${fallback};
        `);
          }
        } else if ((0, types_12.isWithinOneOfThatShouldBeUnion)(options, field)) {
          const oneofName = (0, case_1.maybeSnakeToCamel)(messageDesc.oneofDecl[field.oneofIndex].name, options);
          const oneofNameWithMessage = (0, utils_12.getPropertyAccessor)("message", oneofName);
          const oneofNameWithObject = (0, utils_12.getPropertyAccessor)("object", oneofName);
          const valueName = (0, utils_12.oneofValueName)(fieldName, options);
          const v = readSnippet(`${oneofNameWithObject}.${valueName}`);
          if (currentSwitchTarget !== void 0 && currentSwitchTarget !== oneofNameWithObject) {
            chunks.push((0, ts_poet_1.code)`}`);
            currentSwitchTarget = void 0;
          }
          if (currentSwitchTarget === void 0) {
            chunks.push((0, ts_poet_1.code)`switch (${oneofNameWithObject}?.$case) {`);
          }
          chunks.push((0, ts_poet_1.code)`
        case '${fieldName}': {
          if (${oneofNameWithObject}?.${valueName} !== undefined
              && ${oneofNameWithObject}?.${valueName} !== null) {
            ${oneofNameWithMessage} = { $case: '${fieldName}', ${valueName}: ${v} };
          }
          break;
        }
      `);
          currentSwitchTarget = oneofNameWithObject;
        } else if (readSnippet(`x`).toCodeString([]) == "x") {
          const fallback = (0, types_12.isWithinOneOf)(field) || noDefaultValue ? "undefined" : (0, types_12.defaultValue)(ctx, field);
          chunks.push((0, ts_poet_1.code)`${messageProperty} = ${objectProperty} ?? ${fallback};`);
        } else {
          const fallback = (0, types_12.isWithinOneOf)(field) || noDefaultValue ? "undefined" : (0, types_12.defaultValue)(ctx, field);
          chunks.push((0, ts_poet_1.code)`
        ${messageProperty} = (${objectProperty} !== undefined && ${objectProperty} !== null)
          ? ${readSnippet(`${objectProperty}`)}
          : ${fallback};
      `);
        }
      });
      if (currentSwitchTarget) {
        chunks.push((0, ts_poet_1.code)`}`);
        currentSwitchTarget = "";
      }
      chunks.push((0, ts_poet_1.code)`return message;`);
      chunks.push((0, ts_poet_1.code)`}`);
      return (0, ts_poet_1.joinCode)(chunks, { on: "\n" });
    }
    exports2.contextTypeVar = "Context extends DataLoaders";
    function convertFromObjectKey(ctx, messageDesc, field, variableName) {
      const { keyType, keyField } = (0, types_12.detectMapType)(ctx, messageDesc, field);
      if (keyType.toCodeString([]) === "string") {
        return (0, ts_poet_1.code)`${variableName}`;
      } else if ((0, types_12.isLong)(keyField) && (0, types_12.shouldGenerateJSMapType)(ctx, messageDesc, field)) {
        if (ctx.options.forceLong === options_12.LongOption.LONG) {
          return (0, ts_poet_1.code)`${(0, case_1.capitalize)(keyType.toCodeString([]))}.fromValue(${variableName})`;
        } else if (ctx.options.forceLong === options_12.LongOption.BIGINT) {
          return (0, ts_poet_1.code)`BigInt(${variableName})`;
        } else if (ctx.options.forceLong === options_12.LongOption.STRING) {
          return (0, ts_poet_1.code)`${ctx.utils.globalThis}.String(${variableName})`;
        } else {
          return (0, ts_poet_1.code)`${ctx.utils.globalThis}.Number(${variableName})`;
        }
      } else if (keyField.type === ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_BOOL) {
        return (0, ts_poet_1.code)`${ctx.utils.globalThis}.Boolean(${variableName})`;
      } else {
        return (0, ts_poet_1.code)`${ctx.utils.globalThis}.Number(${variableName})`;
      }
    }
    function convertToObjectKey(ctx, messageDesc, field, variableName) {
      const { keyType, keyField } = (0, types_12.detectMapType)(ctx, messageDesc, field);
      if (keyType.toCodeString([]) === "string") {
        return (0, ts_poet_1.code)`${variableName}`;
      } else if ((0, types_12.isLong)(keyField) && (0, types_12.shouldGenerateJSMapType)(ctx, messageDesc, field)) {
        if (ctx.options.forceLong === options_12.LongOption.LONG) {
          return (0, ts_poet_1.code)`${ctx.utils.longToNumber}(${variableName})`;
        } else if (ctx.options.forceLong === options_12.LongOption.BIGINT) {
          return (0, ts_poet_1.code)`${variableName}.toString()`;
        } else {
          return (0, ts_poet_1.code)`${variableName}`;
        }
      } else if (keyField.type === ts_proto_descriptors_12.FieldDescriptorProto_Type.TYPE_BOOL) {
        return (0, ts_poet_1.code)`${ctx.utils.globalThis}.String(${variableName})`;
      } else {
        return (0, ts_poet_1.code)`${variableName}`;
      }
    }
    function maybeReadonly(options) {
      return options.useReadonlyTypes ? "readonly " : "";
    }
    function maybeAsAny(options) {
      return options.useReadonlyTypes ? " as any" : "";
    }
  }
});

// hardware/generic/goldfish/emulator/ui/aquarium/node_modules/ts-proto/build/src/context.js
var require_context = __commonJS({
  "hardware/generic/goldfish/emulator/ui/aquarium/node_modules/ts-proto/build/src/context.js"(exports2) {
    "use strict";
    Object.defineProperty(exports2, "__esModule", { value: true });
    exports2.createFileContext = createFileContext;
    var ts_proto_descriptors_12 = require_dist();
    function createFileContext(file) {
      const edition = file.edition !== ts_proto_descriptors_12.Edition.EDITION_UNKNOWN ? file.edition : void 0;
      const isEdition = edition !== void 0;
      const isProto3Syntax = file.syntax === "proto3" || file.syntax !== "proto2" && isProto3Edition(edition);
      return {
        isProto3Syntax,
        isEdition,
        edition
      };
    }
    function isProto3Edition(edition) {
      if (edition === void 0) {
        return false;
      }
      return edition === ts_proto_descriptors_12.Edition.EDITION_PROTO3 || edition === ts_proto_descriptors_12.Edition.EDITION_2023 || edition === ts_proto_descriptors_12.Edition.EDITION_2024;
    }
  }
});

// hardware/generic/goldfish/emulator/ui/aquarium/node_modules/ts-proto/build/src/generate-type-registry.js
var require_generate_type_registry = __commonJS({
  "hardware/generic/goldfish/emulator/ui/aquarium/node_modules/ts-proto/build/src/generate-type-registry.js"(exports2) {
    "use strict";
    Object.defineProperty(exports2, "__esModule", { value: true });
    exports2.generateTypeRegistry = generateTypeRegistry;
    var ts_poet_1 = require_build();
    var options_12 = require_options();
    function generateTypeRegistry(ctx) {
      const chunks = [];
      chunks.push(generateMessageType(ctx));
      if ((0, options_12.addTypeToMessages)(ctx.options)) {
        chunks.push((0, ts_poet_1.code)`
    export type UnknownMessage = {$type: string};
  `);
      } else {
        chunks.push((0, ts_poet_1.code)`
    export type UnknownMessage = unknown;
  `);
      }
      chunks.push((0, ts_poet_1.code)`
    export const messageTypeRegistry = new Map<string, MessageType>();
  `);
      chunks.push((0, ts_poet_1.code)` ${ctx.utils.Builtin.ifUsed} ${ctx.utils.DeepPartial.ifUsed}`);
      return (0, ts_poet_1.joinCode)(chunks, { on: "\n\n" });
    }
    function generateMessageType(ctx) {
      const chunks = [];
      chunks.push((0, ts_poet_1.code)`export interface MessageType<Message extends UnknownMessage = UnknownMessage> {`);
      if ((0, options_12.addTypeToMessages)(ctx.options)) {
        chunks.push((0, ts_poet_1.code)`$type: Message['$type'];`);
      } else {
        chunks.push((0, ts_poet_1.code)`$type: string;`);
      }
      if (ctx.options.outputEncodeMethods) {
        const BinaryReader = (0, ts_poet_1.imp)("t:BinaryReader@@bufbuild/protobuf/wire");
        const BinaryWriter = (0, ts_poet_1.imp)("t:BinaryWriter@@bufbuild/protobuf/wire");
        chunks.push((0, ts_poet_1.code)`encode(message: Message, writer?: ${BinaryWriter}): ${BinaryWriter};`);
        chunks.push((0, ts_poet_1.code)`decode(input: ${BinaryReader} | Uint8Array, length?: number): Message;`);
      }
      if (ctx.options.outputJsonMethods) {
        chunks.push((0, ts_poet_1.code)`fromJSON(object: any): Message;`);
        chunks.push((0, ts_poet_1.code)`toJSON(message: Message): unknown;`);
      }
      if (ctx.options.outputPartialMethods) {
        chunks.push((0, ts_poet_1.code)`fromPartial(object: ${ctx.utils.DeepPartial}<Message>): Message;`);
      }
      chunks.push((0, ts_poet_1.code)`}`);
      return (0, ts_poet_1.joinCode)(chunks, { on: "\n" });
    }
  }
});

// hardware/generic/goldfish/emulator/ui/aquarium/node_modules/ts-proto/build/src/plugin.js
Object.defineProperty(exports, "__esModule", { value: true });
var ts_proto_descriptors_1 = require_dist();
var util_1 = require("util");
var utils_1 = require_utils2();
var main_1 = require_main();
var types_1 = require_types();
var context_1 = require_context();
var options_1 = require_options();
var generate_type_registry_1 = require_generate_type_registry();
async function main() {
  const stdin = await (0, utils_1.readToBuffer)(process.stdin);
  const request = ts_proto_descriptors_1.CodeGeneratorRequest.decode(stdin);
  const { protocVersion, tsProtoVersion } = await (0, utils_1.getVersions)(request);
  const options = (0, options_1.optionsFromParameter)(request.parameter);
  const typeMap = (0, types_1.createTypeMap)(request, options);
  const utils = (0, main_1.makeUtils)(options);
  const ctx = { typeMap, options, utils };
  let filesToGenerate;
  if (options.emitImportedFiles) {
    let addFilesUnlessAliased = function(filenames) {
      filenames.filter((name) => !options.M[name]).forEach((name) => {
        if (fileSet.has(name))
          return;
        fileSet.add(name);
        const file = request.protoFile.find((file2) => file2.name === name);
        if (file && file.dependency.length > 0) {
          addFilesUnlessAliased(file.dependency);
        }
      });
    };
    const fileSet = /* @__PURE__ */ new Set();
    addFilesUnlessAliased(request.fileToGenerate);
    filesToGenerate = request.protoFile.filter((file) => fileSet.has(file.name));
  } else {
    filesToGenerate = (0, utils_1.protoFilesToGenerate)(request).filter((file) => !options.M[file.name]);
  }
  const files = await Promise.all(filesToGenerate.map(async (file) => {
    const [path, code] = (0, main_1.generateFile)({ ...ctx, currentFile: (0, context_1.createFileContext)(file) }, file);
    const content = code.toString({ ...(0, options_1.getTsPoetOpts)(options, tsProtoVersion, protocVersion, file.name), path });
    return { name: path, content };
  }));
  if (options.outputTypeRegistry) {
    const utils2 = (0, main_1.makeUtils)(options);
    const ctx2 = { options, typeMap, utils: utils2 };
    const path = "typeRegistry.ts";
    const code = (0, generate_type_registry_1.generateTypeRegistry)(ctx2);
    const content = code.toString({ ...(0, options_1.getTsPoetOpts)(options, tsProtoVersion, protocVersion), path });
    files.push({ name: path, content });
  }
  if (options.outputIndex) {
    for (const [path, code] of (0, utils_1.generateIndexFiles)(filesToGenerate, options)) {
      const content = code.toString({ ...(0, options_1.getTsPoetOpts)(options, tsProtoVersion, protocVersion), path });
      files.push({ name: path, content });
    }
  }
  const response = ts_proto_descriptors_1.CodeGeneratorResponse.fromPartial({
    file: files,
    supportedFeatures: ts_proto_descriptors_1.CodeGeneratorResponse_Feature.FEATURE_PROTO3_OPTIONAL | ts_proto_descriptors_1.CodeGeneratorResponse_Feature.FEATURE_SUPPORTS_EDITIONS,
    minimumEdition: ts_proto_descriptors_1.Edition.EDITION_PROTO2,
    maximumEdition: ts_proto_descriptors_1.Edition.EDITION_2024
  });
  const buffer = ts_proto_descriptors_1.CodeGeneratorResponse.encode(response).finish();
  const write = (0, util_1.promisify)(process.stdout.write).bind(process.stdout);
  await write(Buffer.from(buffer));
}
main().then(() => {
  process.exit(0);
}).catch((e) => {
  process.stderr.write("FAILED!");
  process.stderr.write(e.message);
  process.stderr.write(e.stack);
  process.exit(1);
});
/*! Bundled license information:

ts-poet/build/is-plain-object.js:
  (*!
   * is-plain-object <https://github.com/jonschlinkert/is-plain-object>
   *
   * Copyright (c) 2014-2017, Jon Schlinkert.
   * Released under the MIT License.
   *)
*/
