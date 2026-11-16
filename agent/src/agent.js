// MoneyWiz in-process agent.
//
// Deliberately generic and small: business rules live in the Python layer.
// Every entry point validates names and types *before* touching the app,
// because an Objective-C exception raised inside MoneyWiz terminates it.
// Nothing here is bound to binary hashes, addresses or ivar offsets; classes,
// selectors and properties are resolved by name and checked at call time.
import ObjC from 'frida-objc-bridge';

const C = ObjC.classes;
const MAX_ITEMS = 200000;

// ---------------------------------------------------------------- utilities

function fail(message) { throw new Error(message); }
function need(condition, message) { if (!condition) fail(message); }

function onMain(work) {
  return new Promise((resolve, reject) => {
    ObjC.schedule(ObjC.mainQueue, () => {
      try { resolve(work()); } catch (e) { const err = new Error(e && e.message ? e.message : String(e)); err.name = (e && e.name) || 'Error'; err.stack = e && e.stack; reject(err); }
    });
  });
}

function nsString(value) {
  need(typeof value === 'string' && !value.includes('\0'), 'string argument required');
  return C.NSString.stringWithUTF8String_(Memory.allocUtf8String(value));
}

function isNil(o) { return o === null || o === undefined || (o.handle && o.handle.isNull()); }
function kindOf(o, klass) { return klass !== undefined && o.isKindOfClass_(klass); }

// ---------------------------------------------------------------- app state

function env() {
  const M = C.MoneyWizManager;
  need(M !== undefined, 'MoneyWizManager class not found');
  // +sharedManager creates and configures a manager when none exists yet, so
  // only call it after the app reports it has one. Never heap-scan
  // (ObjC.choose) here: walking malloc zones on the main thread while other
  // threads allocate faulted and wedged MoneyWiz once.
  need(M.respondsToSelector_(ObjC.selector('isInitialized')) && M.isInitialized(),
    'MoneyWiz has not finished starting (manager not initialized)');
  const manager = M.sharedManager();
  const ivars = manager.$ivars;
  need('_cachedCurrentUser' in ivars, 'MoneyWizManager layout changed: _cachedCurrentUser missing');
  const user = ivars._cachedCurrentUser;
  need(!isNil(user), 'No MoneyWiz user is loaded (open the app and its database first)');
  if ('_isFullyInitialized' in ivars) need(ivars._isFullyInitialized, 'MoneyWiz is still initializing');
  const context = user.managedObjectContext();
  need(!isNil(context), 'User has no managed object context');
  return { manager, user, context, model: context.persistentStoreCoordinator().managedObjectModel() };
}

function entity(e, name) {
  need(typeof name === 'string', 'entity name required');
  const d = e.model.entitiesByName().objectForKey_(nsString(name));
  need(!isNil(d), 'Unknown entity: ' + name);
  return d;
}

// ---------------------------------------------------------------- JS <-> ObjC values

function toJS(v, depth = 0) {
  if (isNil(v)) return null;
  if (!(v instanceof ObjC.Object)) return v;
  need(depth < 16, 'value nesting too deep');
  if (kindOf(v, C.NSNull)) return null;
  if (kindOf(v, C.NSString)) return v.toString();
  if (kindOf(v, C.NSDecimalNumber)) return { $dec: v.stringValue().toString() };
  if (kindOf(v, C.NSNumber)) {
    if (kindOf(v, C.__NSCFBoolean)) return Boolean(v.boolValue());
    const raw = v.objCType(), t = typeof raw === 'string' ? raw : raw.readUtf8String(); // bridge returns char* as string
    if (t === 'd' || t === 'f') return Number(v.doubleValue());
    const text = v.stringValue().toString(), number = Number(text);
    return Number.isSafeInteger(number) ? number : { $int: text }; // keep 64-bit ids exact
  }
  if (kindOf(v, C.NSDate)) return { $date: Number(v.timeIntervalSince1970()) };
  if (kindOf(v, C.NSManagedObject)) return ref(v);
  if (kindOf(v, C.NSSet) || kindOf(v, C.NSOrderedSet) || kindOf(v, C.NSArray)) {
    const array = kindOf(v, C.NSArray) ? v : (kindOf(v, C.NSSet) ? v.allObjects() : v.array());
    const n = Number(array.count());
    need(n <= MAX_ITEMS, 'collection too large');
    const out = [];
    for (let i = 0; i < n; i++) out.push(toJS(array.objectAtIndex_(i), depth + 1));
    return out;
  }
  if (kindOf(v, C.NSDictionary)) {
    const out = {}, keys = v.allKeys(), n = Number(keys.count());
    for (let i = 0; i < n; i++) { const k = keys.objectAtIndex_(i); out[k.toString()] = toJS(v.objectForKey_(k), depth + 1); }
    return out;
  }
  if (kindOf(v, C.NSData)) return { $data: Number(v.length()) };
  return { $object: v.$className };
}

function ref(o) {
  const ent = o.entity();
  const out = { $ref: ent.name().toString(), gid: null };
  if (!isNil(ent.attributesByName().objectForKey_(nsString('GID')))) {
    const g = o.valueForKey_(nsString('GID'));
    out.gid = isNil(g) ? null : g.toString();
  }
  return out;
}

// Typed argument union produced by the Python layer:
//   {s} NSString  {n} NSNumber(double)  {ni} NSNumber(int64)  {nb} NSNumber(BOOL)
//   {dec} NSDecimalNumber  {date} NSDate from unix seconds  {ref:[entity,gid]}
//   {arr:[...]} NSArray  {set:[...]} NSSet  {dict:{k:typed}}  {nil:true}
//   {user:true} {manager:true}  {cls:name}  primitives: {b} {i} {d}
const OBJECT_TAGS = ['s', 'n', 'ni', 'nbig', 'nb', 'dec', 'date', 'ref', 'arr', 'set', 'dict', 'nil', 'user', 'manager'];

function tagOf(arg) {
  need(arg !== null && typeof arg === 'object' && !Array.isArray(arg), 'typed argument object required');
  const keys = Object.keys(arg);
  need(keys.length === 1, 'typed argument must have exactly one tag');
  return keys[0];
}

function toObjC(e, arg) {
  const tag = tagOf(arg), v = arg[tag];
  switch (tag) {
    case 's': return nsString(v);
    case 'n': need(typeof v === 'number' && Number.isFinite(v), 'n: finite number'); return C.NSNumber.numberWithDouble_(v);
    case 'ni': need(Number.isSafeInteger(v), 'ni: safe integer'); return C.NSNumber.numberWithLongLong_(v);
    // 64-bit ids (group ids) exceed JS safe integers, so they travel as text.
    case 'nbig': need(typeof v === 'string' && /^-?\d{1,19}$/.test(v), 'nbig: integer string'); return C.NSNumber.numberWithLongLong_(new Int64(v));
    case 'nb': need(typeof v === 'boolean', 'nb: boolean'); return C.NSNumber.numberWithBool_(v);
    case 'dec': {
      need(typeof v === 'string' && /^-?\d{1,18}(\.\d{1,12})?$/.test(v), 'dec: plain decimal string');
      const d = C.NSDecimalNumber.decimalNumberWithString_locale_(nsString(v),
        C.NSLocale.localeWithLocaleIdentifier_(nsString('en_US_POSIX')));
      need(!d.isEqualToNumber_(C.NSDecimalNumber.notANumber()), 'dec: not a number');
      return d;
    }
    case 'date': need(typeof v === 'number' && Number.isFinite(v), 'date: unix seconds'); return C.NSDate.dateWithTimeIntervalSince1970_(v);
    case 'ref': return lookup(e, v[0], v[1]);
    case 'arr': case 'set': {
      need(Array.isArray(v) && v.length <= MAX_ITEMS, tag + ': array required');
      const m = C.NSMutableArray.array();
      for (const item of v) { const o = toObjC(e, item); need(!isNil(o), tag + ': nil element'); m.addObject_(o); }
      return tag === 'set' ? C.NSSet.setWithArray_(m) : m;
    }
    case 'dict': {
      need(v && typeof v === 'object' && !Array.isArray(v), 'dict: object required');
      const m = C.NSMutableDictionary.dictionary();
      for (const [k, item] of Object.entries(v)) { const o = toObjC(e, item); need(!isNil(o), 'dict: nil value'); m.setObject_forKey_(o, nsString(k)); }
      return m;
    }
    case 'nil': return null;
    case 'user': return e.user;
    case 'manager': return e.manager;
    default: fail('not an object argument: ' + tag);
  }
}

// ---------------------------------------------------------------- object lookup

function lookup(e, entityName, gid) {
  need(typeof gid === 'string' && gid.length > 0 && gid.length <= 512, 'GID required');
  const found = fetchObjects(e, { entity: entityName, where: [['GID', 'eq', { s: gid }]], limit: 2 });
  need(found.length === 1, found.length ? 'Duplicate GID ' + gid : entityName + ' not found: ' + gid);
  return found[0];
}

// ---------------------------------------------------------------- key paths

// Walk a dotted key path through the model so that Core Data never sees an
// unknown key. Returns the final property description and whether the path
// crosses a to-many relationship.
function walk(e, entityDesc, keyPath) {
  need(typeof keyPath === 'string' && /^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$/.test(keyPath), 'Invalid key path: ' + keyPath);
  let current = entityDesc, toMany = false, property = null;
  for (const part of keyPath.split('.')) {
    need(!isNil(current), 'Key path continues past an attribute: ' + keyPath);
    property = current.propertiesByName().objectForKey_(nsString(part));
    need(!isNil(property), `Unknown property ${current.name()}.${part}`);
    if (kindOf(property, C.NSRelationshipDescription)) {
      toMany = toMany || Boolean(property.isToMany());
      current = property.destinationEntity();
    } else current = null;
  }
  return { property, toMany };
}

const OPS = { lt: 0, le: 1, gt: 2, ge: 3, eq: 4, ne: 5, begins: 8, in: 10, contains: 99 };

function checkConstant(property, op, arg) {
  if (kindOf(property, C.NSRelationshipDescription)) {
    need(['eq', 'ne', 'in'].includes(op), 'relationship supports eq/ne/in');
    const ok = a => { const t = tagOf(a); return t === 'ref' || t === 'nil' || t === 'user'; };
    need(op === 'in' ? tagOf(arg) === 'arr' && arg.arr.every(ok) : ok(arg), 'relationship compares with ref/nil');
    return;
  }
  const type = Number(property.attributeType());
  const allowed = type === 700 ? ['s'] : type === 900 ? ['date'] : type === 800 ? ['nb', 'ni', 'n'] :
    (type >= 100 && type <= 600) ? ['n', 'ni', 'nbig', 'dec'] : [];
  need(allowed.length, 'attribute type not filterable: ' + property.name());
  const ok = a => tagOf(a) === 'nil' || allowed.includes(tagOf(a));
  need(op === 'in' ? tagOf(arg) === 'arr' && arg.arr.every(ok) : ok(arg), `wrong constant type for ${property.name()}`);
  if (op === 'contains' || op === 'begins') need(type === 700, 'contains/begins need a string attribute');
}

function predicate(e, entityDesc, where) {
  if (!where || where.length === 0) return null;
  const parts = where.map(clause => {
    if (clause && !Array.isArray(clause) && clause.or) {
      const subs = C.NSMutableArray.array();
      for (const p of clause.or.map(c => predicate(e, entityDesc, [c]))) subs.addObject_(p);
      return C.NSCompoundPredicate.orPredicateWithSubpredicates_(subs);
    }
    need(Array.isArray(clause) && clause.length === 3, 'where clause is [keyPath, op, value]');
    const [keyPath, op, value] = clause;
    need(op in OPS, 'Unknown operator: ' + op);
    const { property, toMany } = walk(e, entityDesc, keyPath);
    checkConstant(property, op, value);
    const left = C.NSExpression.expressionForKeyPath_(nsString(keyPath));
    const constant = toObjC(e, value);
    const right = C.NSExpression.expressionForConstantValue_(constant);
    const options = (op === 'contains' || op === 'begins') ? 3 : 0; // case + diacritic insensitive
    return C.NSComparisonPredicate.predicateWithLeftExpression_rightExpression_modifier_type_options_(
      left, right, toMany ? 2 : 0, OPS[op], options);
  });
  const array = C.NSMutableArray.array();
  for (const p of parts) array.addObject_(p);
  return C.NSCompoundPredicate.andPredicateWithSubpredicates_(array);
}

function fetchRequest(e, spec) {
  const entityDesc = entity(e, spec.entity);
  const request = C.NSFetchRequest.alloc().init().autorelease();
  request.setEntity_(entityDesc);
  const p = predicate(e, entityDesc, spec.where);
  if (p) request.setPredicate_(p);
  if (spec.sort && spec.sort.length) {
    const sorts = C.NSMutableArray.array();
    for (const [keyPath, ascending] of spec.sort) {
      const { toMany } = walk(e, entityDesc, keyPath);
      need(!toMany, 'cannot sort on a to-many path');
      sorts.addObject_(C.NSSortDescriptor.sortDescriptorWithKey_ascending_(nsString(keyPath), Boolean(ascending)));
    }
    request.setSortDescriptors_(sorts);
  }
  return request;
}

function fetchObjects(e, spec) {
  const request = fetchRequest(e, spec);
  if (spec.limit) { need(Number.isSafeInteger(spec.limit) && spec.limit > 0, 'limit'); request.setFetchLimit_(spec.limit); }
  if (spec.offset) { need(Number.isSafeInteger(spec.offset) && spec.offset >= 0, 'offset'); request.setFetchOffset_(spec.offset); }
  const error = Memory.alloc(Process.pointerSize).writePointer(NULL);
  const result = e.context.executeFetchRequest_error_(request, error);
  need(!isNil(result), 'Fetch failed: ' + (error.readPointer().isNull() ? 'unknown' : new ObjC.Object(error.readPointer()).localizedDescription()));
  const out = [], n = Number(result.count());
  for (let i = 0; i < n; i++) out.push(result.objectAtIndex_(i));
  return out;
}

function count(e, spec) {
  const request = fetchRequest(e, spec);
  const error = Memory.alloc(Process.pointerSize).writePointer(NULL);
  const n = Number(e.context.countForFetchRequest_error_(request, error));
  need(n >= 0 && n !== 0x7fffffffffffffff, 'Count failed');
  return n;
}

// ---------------------------------------------------------------- field reads

// Read one key. Model properties go through KVC; other keys must name an
// argument-less method the object responds to (computed values such as
// balances), so valueForKey: never raises for an undefined key.
function readKey(o, key) {
  if (key === '$entity') return kindOf(o, C.NSManagedObject) ? o.entity().name() : null;
  if (key === '$class') return nsString(o.$className);
  need(/^[A-Za-z_][A-Za-z0-9_]*$/.test(key), 'Invalid key: ' + key);
  if (kindOf(o, C.NSManagedObject) && !isNil(o.entity().propertiesByName().objectForKey_(nsString(key))))
    return o.valueForKey_(nsString(key));
  if (o.respondsToSelector_(ObjC.selector(key))) {
    const method = o[key];
    need(method && method.argumentTypes.length === 2, 'Not an argument-less getter: ' + key);
    return o.valueForKey_(nsString(key));
  }
  return undefined; // property does not exist on this concrete object
}

function readPath(o, parts) {
  if (isNil(o)) return null;
  if (parts.length === 0) return toJS(o);
  if (kindOf(o, C.NSSet) || kindOf(o, C.NSOrderedSet) || kindOf(o, C.NSArray)) {
    const array = kindOf(o, C.NSArray) ? o : (kindOf(o, C.NSSet) ? o.allObjects() : o.array());
    const out = [], n = Number(array.count());
    for (let i = 0; i < n; i++) out.push(readPath(array.objectAtIndex_(i), parts));
    return out;
  }
  const value = readKey(o, parts[0]);
  if (value === undefined) return undefined;
  if (!(value instanceof ObjC.Object)) return value;
  return readPath(value, parts.slice(1));
}

function serialize(o, fields) {
  const out = ref(o);
  out.$ref = o.entity().name().toString();
  for (const field of fields || []) {
    const value = readPath(o, field.split('.'));
    if (value !== undefined) out[field] = value;
  }
  return out;
}

// ---------------------------------------------------------------- invocation

function parseTypes(encoding) {
  const types = [];
  let i = 0;
  while (i < encoding.length) {
    while ('rnNoORV'.includes(encoding[i])) i++;
    let t = encoding[i];
    if (t === '@' && encoding[i + 1] === '?') { t = '@?'; i += 2; }
    else if (t === '@' && encoding[i + 1] === '"') { i = encoding.indexOf('"', i + 2) + 1; }
    else if (t === '^' || t === '{' || t === '[' || t === '(') return null; // not supported
    else i++;
    types.push(t);
    while (i < encoding.length && /[0-9-]/.test(encoding[i])) i++;
  }
  return types; // [return, self, _cmd, ...args]
}

const PRIMITIVE = { B: ['b'], c: ['b', 'i'], i: ['i'], s: ['i'], l: ['i'], q: ['i'], I: ['i'], S: ['i'], L: ['i'], Q: ['i'], d: ['d', 'i'], f: ['d', 'i'] };

function target(e, spec) {
  if (spec === 'manager') return e.manager;
  if (spec === 'user') return e.user;
  if (spec && spec.cls) { const k = C[spec.cls]; need(k !== undefined, 'Unknown class ' + spec.cls); return k; }
  if (spec && spec.ref) return lookup(e, spec.ref[0], spec.ref[1]);
  fail('Unknown invocation target');
}

function invokeOn(e, spec) {
  const receiver = target(e, spec.target);
  const selector = spec.selector;
  need(typeof selector === 'string' && /^[A-Za-z_][A-Za-z0-9_:]*$/.test(selector), 'Invalid selector');
  need(receiver.respondsToSelector_(ObjC.selector(selector)), `${receiver.$className} does not respond to ${selector}`);
  const isClass = spec.target && spec.target.cls;
  const method = receiver[(isClass ? '+ ' : '- ') + selector];
  need(method, 'Method not resolvable: ' + selector);
  const types = parseTypes(method.types);
  need(types !== null, 'Unsupported method signature: ' + method.types);
  if (spec.encoding) need(method.types.replace(/[0-9]/g, '') === spec.encoding.replace(/[0-9]/g, ''),
    `Signature of ${selector} changed: ${method.types}`);
  const argTypes = types.slice(3), args = spec.args || [];
  need(args.length === argTypes.length, `${selector} takes ${argTypes.length} arguments, got ${args.length}`);
  const values = args.map((arg, index) => {
    const t = argTypes[index], tag = tagOf(arg);
    if (t === '@') { need(OBJECT_TAGS.includes(tag), `argument ${index} of ${selector} must be an object`); return toObjC(e, arg); }
    if (t === '#') { need(tag === 'cls' && C[arg.cls] !== undefined, `argument ${index} must be a class`); return C[arg.cls]; }
    need(PRIMITIVE[t] && PRIMITIVE[t].includes(tag), `argument ${index} of ${selector} has type ${t}, got ${tag}`);
    if (tag === 'b') { need(typeof arg.b === 'boolean', 'b: boolean'); return arg.b; }
    if (tag === 'i') { need(Number.isSafeInteger(arg.i), 'i: integer'); return arg.i; }
    need(typeof arg.d === 'number' && Number.isFinite(arg.d), 'd: number'); return arg.d;
  });
  const result = method.apply(receiver, values);
  if (types[0] === 'v') return null;
  if (types[0] === '@') return isNil(result) ? null : (kindOf(result, C.NSManagedObject) ? serialize(result, spec.fields) : toJS(result));
  return result;
}

function changeSummary(set) {
  const out = {}, array = set.allObjects(), n = Number(array.count());
  for (let i = 0; i < Math.min(n, 500); i++) {
    const o = array.objectAtIndex_(i), name = o.entity().name().toString();
    const keys = o.changedValues().allKeys(), k = Number(keys.count());
    const entry = out[name] || (out[name] = { count: 0, keys: {} });
    entry.count++;
    for (let j = 0; j < k; j++) entry.keys[keys.objectAtIndex_(j).toString()] = true;
  }
  for (const entry of Object.values(out)) entry.keys = Object.keys(entry.keys).sort();
  return out;
}

// Objects can be registered as updated without any value change (MoneyWiz
// touches investment holdings at launch). Only real edits count as pending.
function changedCount(set) {
  const array = set.allObjects(), n = Number(array.count());
  let changed = 0;
  for (let i = 0; i < n; i++) if (Number(array.objectAtIndex_(i).changedValues().count()) > 0) changed++;
  return changed;
}

function contextState(e, detail = false) {
  const inserted = e.context.insertedObjects(), updated = e.context.updatedObjects(), deleted = e.context.deletedObjects();
  const out = { has_changes: Boolean(e.context.hasChanges()), inserted: Number(inserted.count()), updated: Number(updated.count()),
    updated_with_values: changedCount(updated), deleted: Number(deleted.count()) };
  out.pending = out.inserted + out.deleted + out.updated_with_values;
  if (detail) out.detail = { inserted: changeSummary(inserted), updated: changeSummary(updated), deleted: changeSummary(deleted) };
  return out;
}

// A business method saves the whole context, so writing while the app holds
// its own unsaved objects would commit those too. Some are short-lived: after
// data changes, MWPortfolioStoryModel inserts a CustomReport to compute the
// portfolio overview and deletes it about a second later. The error is named
// so the caller can wait for that and retry; nothing has been called yet.
function requireClean(e, state) {
  if (state.pending === 0) return;
  const detail = contextState(e, true).detail, parts = [];
  for (const [kind, groups] of Object.entries(detail))
    for (const [name, entry] of Object.entries(groups))
      if (kind !== 'updated' || entry.keys.length) parts.push(`${kind} ${name} x${entry.count}`);
  const hint = detail.inserted.CustomReport ? 'a report MoneyWiz is still calculating' : 'an editor may be open in MoneyWiz';
  const error = new Error(`MoneyWiz has unsaved changes: ${parts.join(', ')} (${hint})`);
  error.name = 'UnsavedChanges';
  throw error;
}

// Some app observers do nothing but put up a dialog in reaction to a business
// call (MoneyWiz answers every new transfer with a "transfer succeeded" alert).
// A call may name such handlers in spec.quiet: each must be a void method with
// one object argument, and it is skipped only while that call runs. Every
// other invocation, including the app's own, goes through unchanged, and Frida
// removes the replacement when the agent unloads.
const quietHandlers = new Map(), quietActive = new Set();
function quietHandler(className, selector) {
  need(/^[A-Za-z_][A-Za-z0-9_]*$/.test(className) && /^[A-Za-z_][A-Za-z0-9_]*:$/.test(selector), 'Invalid quiet handler');
  const key = `${className} ${selector}`;
  if (!quietHandlers.has(key)) {
    const k = C[className], method = k === undefined ? undefined : k['- ' + selector];
    let installed = false;
    if (method !== undefined && method.types === 'v24@0:8@16') {
      const target = method.implementation;
      const original = new NativeFunction(target, 'void', ['pointer', 'pointer', 'pointer']);
      Interceptor.replace(target, new NativeCallback((self, cmd, arg) => {
        if (!quietActive.has(key)) original(self, cmd, arg);
      }, 'void', ['pointer', 'pointer', 'pointer']));
      installed = true;
    }
    quietHandlers.set(key, installed);
  }
  return quietHandlers.get(key) ? key : null;
}

function quietly(spec, work) {
  need(spec.quiet === undefined || Array.isArray(spec.quiet), 'quiet must be a list');
  // A handler missing in this build only means its dialog is not suppressed.
  const keys = (spec.quiet || []).map(([c, s]) => quietHandler(c, s)).filter(Boolean);
  keys.forEach(k => quietActive.add(k));
  try { return work(); } finally { keys.forEach(k => quietActive.delete(k)); }
}

function save(e) {
  const before = contextState(e);
  if (before.pending === 0) return { saved: false, before, after: before };
  const ok = invokeOn(e, { target: 'manager', selector: 'saveData', args: [], encoding: 'B16@0:8' });
  return { saved: Boolean(ok), before, after: contextState(e) };
}

// ---------------------------------------------------------------- rpc

rpc.exports = {
  ping() { return { pid: Process.id }; },

  status() {
    return onMain(() => {
      const e = env();
      const info = C.NSBundle.mainBundle().infoDictionary();
      const store = e.user.objectID().persistentStore();
      return {
        pid: Process.id,
        version: toJS(info.objectForKey_(nsString('CFBundleShortVersionString'))),
        build: toJS(info.objectForKey_(nsString('CFBundleVersion'))),
        store_path: store.URL().path().toString(),
        store_uuid: store.identifier().toString(),
        user: serialize(e.user, []),
        context: contextState(e, true),
        model_version: toJS(e.model.versionIdentifiers()),
      };
    });
  },

  // spec: {entity, where, sort, limit, offset, fields, count}
  query(spec) {
    return onMain(() => {
      const e = env();
      const objects = fetchObjects(e, spec);
      const out = { items: objects.map(o => serialize(o, spec.fields)) };
      if (spec.count) out.total = count(e, spec);
      return out;
    });
  },

  count(spec) { return onMain(() => count(env(), spec)); },

  // spec: {target, selector, args, encoding?, fields?, save?, allow_dirty?}
  invoke(spec) {
    return onMain(() => {
      const e = env();
      const before = contextState(e);
      if (!spec.allow_dirty) requireClean(e, before);
      const value = quietly(spec, () => invokeOn(e, spec));
      const after = contextState(e);
      const out = { value, context_before: before, context_after: after };
      if (spec.save && after.pending > 0) out.save = save(e);
      return out;
    });
  },

  // Several calls in one main-queue turn; stops at the first failure.
  batch(specs) {
    return onMain(() => {
      const e = env();
      requireClean(e, contextState(e));
      const results = [];
      for (const spec of specs) {
        if (spec.query) {
          const objects = fetchObjects(e, spec.query);
          results.push({ items: objects.map(o => serialize(o, spec.query.fields)) });
        } else results.push({ value: quietly(spec, () => invokeOn(e, spec)) });
      }
      const out = { results, context_after: contextState(e) };
      if (specs.some(s => s.save) && out.context_after.pending > 0) out.save = save(e);
      return out;
    });
  },

  save() { return onMain(() => save(env())); },

  methods(className, filter) {
    const k = C[className];
    need(k !== undefined, 'Unknown class ' + className);
    return k.$ownMethods.filter(m => !filter || m.toLowerCase().includes(filter.toLowerCase()))
      .map(m => [m, k[m].types]);
  },
};
