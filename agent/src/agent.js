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
const OBJECT_TAGS = ['s', 'n', 'ni', 'nbig', 'nb', 'dec', 'date', 'ref', 'arr', 'set', 'dict', 'nil', 'user', 'manager', 'prev'];

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
    // {prev: i}: the object returned by the i-th call earlier in the same batch.
    case 'prev': {
      need(e.produced && Number.isSafeInteger(v) && v >= 0 && v < e.produced.length, 'prev: no such earlier result');
      const o = e.produced[v];
      need(!isNil(o), 'prev: that call returned nothing');
      return o;
    }
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

// What valueForKey: can box from a getter: objects, classes, scalars, structs.
// For a pointer, selector, union or void it raises, and that would abort MoneyWiz.
const KVC_RETURN = /^[rnNoORV]*[@#cCsSiIlLqQfdB{]/;

// Read one key. Model properties go through KVC; other keys must name an
// argument-less method the object responds to (computed values such as
// balances) whose return type KVC can box, so valueForKey: never raises.
function readKey(o, key) {
  if (key === '$entity') return kindOf(o, C.NSManagedObject) ? o.entity().name() : null;
  if (key === '$class') return nsString(o.$className);
  need(/^[A-Za-z_][A-Za-z0-9_]*$/.test(key), 'Invalid key: ' + key);
  if (kindOf(o, C.NSManagedObject) && !isNil(o.entity().propertiesByName().objectForKey_(nsString(key))))
    return o.valueForKey_(nsString(key));
  if (o.respondsToSelector_(ObjC.selector(key))) {
    const method = o[key];
    need(method && method.argumentTypes.length === 2, 'Not an argument-less getter: ' + key);
    need(KVC_RETURN.test(method.types), `${o.$className} ${key} returns ${method.types}, which cannot be read`);
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

// Call one method. Returns what it returned and its return type, unconverted,
// so a caller can save what the call changed before anything else can fail.
function callOn(e, spec) {
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
  if (e.produced && types[0] === '@') e.produced.push(isNil(result) ? null : result);
  return { result, type: types[0] };
}

function present(called, fields) {
  const { result, type } = called;
  if (type === 'v') return null;
  if (type === '@') return isNil(result) ? null : (kindOf(result, C.NSManagedObject) ? serialize(result, fields) : toJS(result));
  return result;
}

function invokeOn(e, spec) { return present(callOn(e, spec), spec.fields); }

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


// Whether a deleted object's row is still in the store. Merging another
// context's save (an iCloud import) invokes delete: on the objects it removed,
// so they sit in deletedObjects although the store no longer has them; saving
// them changes nothing. Anything uncertain counts as still stored.
const MAX_STORE_CHECKS = 200;
function inStore(e, o) {
  const id = o.objectID();
  if (id.isTemporaryID()) return true;
  const request = C.NSFetchRequest.alloc().init().autorelease();
  request.setEntity_(o.entity());
  request.setIncludesPendingChanges_(false);
  request.setPredicate_(C.NSComparisonPredicate.predicateWithLeftExpression_rightExpression_modifier_type_options_(
    C.NSExpression.expressionForEvaluatedObject(), C.NSExpression.expressionForConstantValue_(id), 0, 4, 0));
  const error = Memory.alloc(Process.pointerSize).writePointer(NULL);
  const n = Number(e.context.countForFetchRequest_error_(request, error));
  return !(n === 0 && error.readPointer().isNull());
}

function uri(o) { return o.objectID().URIRepresentation().absoluteString().toString(); }

// MoneyWiz's own maintenance of its description-autocomplete history:
// creating a history item schedules -[DataAccessor purge1YearOldStringHistoryItems]
// (performSelectorOnce:afterDelay:), which deletes year-old StringHistoryItems,
// and with them their category links, without saving; the next save commits
// it. Those deletions are not anyone's unsaved edit.
function isHistoryUpkeep(o) {
  const name = o.entity().name().toString();
  if (name === 'StringHistoryItem') return true;
  if (name !== 'CategoryAssigment') return false;
  const keys = ['stringHistoryItem', 'transaction', 'scheduledTransacition'];
  const props = o.entity().propertiesByName();
  if (keys.some(k => isNil(props.objectForKey_(nsString(k))))) return false;
  const committed = o.committedValuesForKeys_(arrayOf(keys.map(nsString)));
  const was = k => { const v = committed.objectForKey_(nsString(k)); return !isNil(v) && !kindOf(v, C.NSNull); };
  return was('stringHistoryItem') && !was('transaction') && !was('scheduledTransacition');
}

// Split deletedObjects into real pending deletions, and the URIs of those that
// are already gone from the store or are history upkeep (see above).
function splitDeleted(e) {
  const array = e.context.deletedObjects().allObjects(), n = Number(array.count()), live = [], gone = new Set();
  let upkeep = 0;
  for (let i = 0; i < n; i++) {
    const o = array.objectAtIndex_(i);
    if (n <= MAX_STORE_CHECKS * 10 && isHistoryUpkeep(o)) { gone.add(uri(o)); upkeep++; continue; }
    if (n > MAX_STORE_CHECKS || inStore(e, o)) live.push(o); else gone.add(uri(o));
  }
  return { live, gone, upkeep };
}

function members(value) {
  if (isNil(value) || kindOf(value, C.NSNull)) return [];
  const array = kindOf(value, C.NSSet) ? value.allObjects() : kindOf(value, C.NSOrderedSet) ? value.array() : value;
  const out = [];
  for (let i = 0; i < Number(array.count()); i++) out.push(uri(array.objectAtIndex_(i)));
  return out;
}

// The same merge also drops the deleted rows from related objects' loaded
// relationships, which marks those objects updated. Such an object is only
// merge residue when every change is a relationship losing members that are
// already gone from the store; any attribute change or added member is real.
function isResidue(o, gone) {
  if (gone.size === 0) return false;
  const changed = o.changedValues(), keys = changed.allKeys(), relationships = o.entity().relationshipsByName();
  const committed = o.committedValuesForKeys_(keys);
  for (let i = 0; i < Number(keys.count()); i++) {
    const key = keys.objectAtIndex_(i), relationship = relationships.objectForKey_(key);
    if (isNil(relationship)) return false;
    const now = members(changed.objectForKey_(key)), before = members(committed.objectForKey_(key));
    if (now.some(u => !before.includes(u))) return false;
    if (before.some(u => !now.includes(u) && !gone.has(u))) return false;
  }
  return true;
}

// Objects can be registered as updated without any value change (MoneyWiz
// touches investment holdings at launch); only real edits count.
function realUpdates(e, gone) {
  const array = e.context.updatedObjects().allObjects(), out = [];
  for (let i = 0; i < Number(array.count()); i++) {
    const o = array.objectAtIndex_(i);
    if (Number(o.changedValues().count()) > 0 && !isResidue(o, gone)) out.push(o);
  }
  return out;
}

function arrayOf(objects) {
  const m = C.NSMutableArray.array();
  for (const o of objects) m.addObject_(o);
  return m;
}

function contextState(e, detail = false) {
  const inserted = e.context.insertedObjects(), updated = e.context.updatedObjects();
  const { live, gone, upkeep } = splitDeleted(e), real = realUpdates(e, gone);
  const out = { has_changes: Boolean(e.context.hasChanges()), inserted: Number(inserted.count()), updated: Number(updated.count()),
    updated_with_values: real.length, deleted: live.length, already_deleted_in_store: gone.size - upkeep,
    history_upkeep: upkeep };
  out.pending = out.inserted + out.deleted + out.updated_with_values;
  if (detail) out.detail = { inserted: changeSummary(inserted), updated: changeSummary(C.NSSet.setWithArray_(arrayOf(real))),
    deleted: changeSummary(C.NSSet.setWithArray_(arrayOf(live))) };
  return out;
}

// The objects a save would actually write: inserted, really updated, and
// deleted ones still in the store.
function pendingObjects(e) {
  const out = [], inserted = e.context.insertedObjects().allObjects();
  for (let i = 0; i < Number(inserted.count()); i++) out.push(inserted.objectAtIndex_(i));
  const { live, gone } = splitDeleted(e);
  return out.concat(realUpdates(e, gone), live);
}

// What is pending, object by object with the keys it changed, to tell what one
// call added to it.
function pendingKeys(e) {
  return new Set(pendingObjects(e).map(o => {
    const keys = o.changedValues().allKeys(), names = [];
    for (let i = 0; i < Number(keys.count()); i++) names.push(keys.objectAtIndex_(i).toString());
    return `${uri(o)} ${names.sort().join(',')}`;
  }));
}

// A method that only answers a question (a validator, a computed figure). It
// may run while the app holds unsaved changes, because nothing is saved, but
// it must not add any: a later save would commit them (a report calculated in
// the main context records scheduled transactions, for one). When the context
// held nothing at all before, what the call left is exactly its own and is
// rolled back; otherwise it cannot be told apart and stays. Either way the
// answer is refused, by name.
function answer(e, spec) {
  const hadChanges = Boolean(e.context.hasChanges()), before = hadChanges ? pendingKeys(e) : new Set();
  const called = callOn(e, spec);
  if (!e.context.hasChanges()) return { value: present(called, spec.fields) };
  const added = [...pendingKeys(e)].filter(k => !before.has(k));
  if (added.length === 0) return { value: present(called, spec.fields) };
  if (!hadChanges) e.context.rollback();
  const error = new Error(`MoneyWiz changed ${added.length} objects while answering ${spec.selector}; ` +
    (hadChanges ? 'they are left unsaved, next to changes of its own' : 'that was undone') +
    '. Treat this method as a write.');
  error.name = 'ReadChangedData';
  throw error;
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
// one object argument. For the duration of that call only, the method's
// implementation is swapped for a no-op and then put back, all within the same
// main-queue turn in which the observers run. Nothing stays patched between
// calls, so several agents attached at once (two MCP clients) cannot leave one
// another's code patches pointing into unloaded memory, which persistent
// Interceptor patches did.
// The stand-in records what the observer was told (the notification's name and
// object, e.g. the budgets MoneyWiz would warn about), so the caller can report
// it. It must never throw back into MoneyWiz's frames.
let captured = [];
const MAX_NOTICES = 20;
const MAX_ATTACHMENT = 25 * 1024 * 1024;
const NSTemporaryDirectory = new NativeFunction(Module.getGlobalExportByName('NSTemporaryDirectory'), 'pointer', []);
function stagingDir() { return new ObjC.Object(NSTemporaryDirectory()).toString().replace(/\/$/, '') + '/moneywiz-mcp'; }

const NOOP = new NativeCallback((self, cmd, arg) => {
  try {
    if (captured.length >= MAX_NOTICES || arg.isNull()) return;
    const note = new ObjC.Object(arg);
    if (!kindOf(note, C.NSNotification)) return;
    captured.push({ handler: `${new ObjC.Object(self).$className} ${ObjC.selectorAsString(cmd)}`,
                    name: note.name().toString(), object: toJS(note.object()) });
  } catch (e) { /* recording is best effort; the dialog is skipped either way */ }
}, 'void', ['pointer', 'pointer', 'pointer']);

function quietMethod(className, selector) {
  need(/^[A-Za-z_][A-Za-z0-9_]*$/.test(className) && /^[A-Za-z_][A-Za-z0-9_]*:$/.test(selector), 'Invalid quiet handler');
  const k = C[className], method = k === undefined ? undefined : k['- ' + selector];
  return method !== undefined && method.types === 'v24@0:8@16' ? method : null;
}

function quietly(spec, work) {
  need(spec.quiet === undefined || Array.isArray(spec.quiet), 'quiet must be a list');
  // A handler missing in this build only means its dialog is not suppressed.
  const methods = (spec.quiet || []).map(([c, s]) => quietMethod(c, s)).filter(Boolean);
  const saved = methods.map(m => m.implementation);
  methods.forEach(m => { m.implementation = NOOP; });
  try { return work(); } finally { methods.forEach((m, i) => { m.implementation = saved[i]; }); }
}

function takeNotices() { const out = captured; captured = []; return out; }

function save(e) {
  const before = contextState(e);
  if (before.pending === 0) return { saved: false, before, after: before };
  const ok = invokeOn(e, { target: 'manager', selector: 'saveData', args: [], encoding: 'B16@0:8' });
  return { saved: Boolean(ok), before, after: contextState(e) };
}

// ---------------------------------------------------------------- reports

// The 15-argument creator MoneyWiz's own report calculation uses.
const REPORT_CREATE = 'createReportForUser:predefinedReportType:mainDataType:filters:chartType:chartOptions:' +
  'chartBreakPeriod:predefinedDatesPeriod:fromDate:toDate:calculateData:oldRootGroup:' +
  'includeScheduledTransactions:includeArchivedAccounts:delegate:';
const pending = new Set(); // blocks handed to another queue, kept alive until they ran

// Methods the caller recorded, checked before anything runs: [[class, "- selector", encoding]].
// Returns a lookup that refuses any method the caller did not list.
function checkedCalls(list) {
  need(Array.isArray(list), 'calls: [[class, "- selector", encoding], ...]');
  const found = new Map();
  for (const [className, selector, encoding] of list) {
    need(typeof className === 'string' && /^[-+] [A-Za-z_][A-Za-z0-9_:]*$/.test(selector), 'Invalid call ' + selector);
    const k = C[className];
    need(k !== undefined, 'Class not found: ' + className);
    const m = k[selector];
    need(m !== undefined, `${className} ${selector}: missing in this MoneyWiz build`);
    need(m.types.replace(/[0-9]/g, '') === String(encoding).replace(/[0-9]/g, ''),
      `Signature of ${className} ${selector} changed: ${m.types}`);
    found.set(`${className} ${selector}`, m);
  }
  return (className, selector) => {
    const m = found.get(`${className} ${selector}`);
    need(m !== undefined, `${className} ${selector} was not checked`);
    return m;
  };
}

// A tree of plain result objects (a report's chart and its groups), read by
// named shapes: {fields: [key paths], each: {key: [shape, limit]}}. A
// collection under `each` is read item by item, up to `limit` items, and its
// size is reported as key + '#'.
function shaped(o, shapes, name, limit, budget) {
  if (isNil(o)) return null;
  const shape = shapes[name];
  need(shape, 'Unknown result shape ' + name);
  if (kindOf(o, C.NSArray) || kindOf(o, C.NSSet) || kindOf(o, C.NSOrderedSet)) {
    const array = kindOf(o, C.NSArray) ? o : (kindOf(o, C.NSSet) ? o.allObjects() : o.array());
    const n = Number(array.count()), out = [];
    for (let i = 0; i < (limit === null || limit === undefined ? n : Math.min(n, limit)); i++)
      out.push(shaped(array.objectAtIndex_(i), shapes, name, null, budget));
    return out;
  }
  need(--budget.nodes >= 0, 'Report too large to return');
  const out = {};
  for (const field of shape.fields || []) {
    const v = readPath(o, field.split('.'));
    if (v !== undefined) out[field] = v;
  }
  for (const [key, [sub, max]] of Object.entries(shape.each || {})) {
    const v = readKey(o, key);
    if (v === undefined || isNil(v)) continue;
    out[key] = shaped(v, shapes, sub, max, budget);
    if (kindOf(v, C.NSArray) || kindOf(v, C.NSSet) || kindOf(v, C.NSOrderedSet)) out[key + '#'] = Number(v.count());
  }
  return out;
}

// ---------------------------------------------------------------- app lifecycle

// The server can run MoneyWiz by itself: launched hidden, kept out of the Dock
// and Cmd-Tab while nobody looks at it, and quit when idle (lifecycle.py).
// These only act while MoneyWiz is hidden, inactive and has no window shown.
function windowState() {
  const app = C.NSApplication.sharedApplication(), windows = app.windows();
  let shown = 0;
  for (let i = 0; i < Number(windows.count()); i++) {
    const w = windows.objectAtIndex_(i);
    if (w.isVisible() || w.isMiniaturized()) shown++;
  }
  const out = { active: Boolean(app.isActive()), hidden: Boolean(app.isHidden()), shown_windows: shown,
    background: Number(app.activationPolicy()) === 1 };
  out.in_use = out.active || !out.hidden || shown > 0;
  return [app, out];
}

// MoneyWiz's own record of iCloud work (MWDatabaseManager; both counters are
// read under its event lock). Quitting while it exports would only delay the
// upload to the next launch, but there is no reason to.
function cloudState(method) {
  const db = method('MWDatabaseManager', '+ sharedManager').call(C.MWDatabaseManager);
  need(!isNil(db), 'MoneyWiz has no database manager');
  const out = { events: Number(method('MWDatabaseManager', '- activeCloudKitEventCount').call(db)),
    exporting: Boolean(method('MWDatabaseManager', '- hasActiveExportEvents').call(db)),
    startup_sync: Boolean(method('MWDatabaseManager', '- isStartupSyncActivityInProgress').call(db)) };
  out.busy = out.events > 0 || out.exporting || out.startup_sync;
  return out;
}

// ---------------------------------------------------------------- rpc

rpc.exports = {
  // Read-only. Window state is there from launch; the ledger and iCloud
  // parts once MoneyWiz has finished starting.
  appState(calls) {
    return onMain(() => {
      const [, out] = windowState();
      try {
        const e = env();
        out.pending = contextState(e).pending;
        out.cloud = cloudState(checkedCalls(calls));
      } catch (err) { out.not_ready = err.message; }
      return out;
    });
  },

  // Keep MoneyWiz out of the Dock and Cmd-Tab (accessory policy) or put it back.
  background(on) {
    return onMain(() => {
      need(typeof on === 'boolean', 'background: true or false');
      const [app, state] = windowState();
      if (on && state.in_use) return { background: state.background, refused: 'MoneyWiz is in use' };
      if (state.background !== on) app.setActivationPolicy_(on ? 1 : 0);
      return { background: Number(app.activationPolicy()) === 1 };
    });
  },

  // MoneyWiz's own quit (as Cmd-Q: its applicationWillTerminate: saves and
  // updates the Dock badge), only when nobody is using it, nothing is unsaved
  // and iCloud is idle. It runs half a second after replying, from a run-loop
  // timer of the app's own (no JavaScript involved), so the caller can detach
  // first: an attached session holds up the exit by about 30 s.
  quit(calls) {
    return onMain(() => {
      const e = env(), method = checkedCalls(calls), [, state] = windowState();
      if (state.in_use) return { quit: false, reason: 'MoneyWiz is in use' };
      const pending = contextState(e).pending;
      if (pending) return { quit: false, reason: `MoneyWiz holds ${pending} unsaved changes` };
      const cloud = cloudState(method);
      if (cloud.busy) return { quit: false, reason: 'iCloud sync in progress', cloud };
      const app = C.NSApplication.sharedApplication();
      app.performSelector_withObject_afterDelay_(ObjC.selector('terminate:'), NULL, 0.5);
      return { quit: true };
    });
  },

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

  // Many counts in one main-queue turn (what uses each category, say): [spec, ...] -> [n, ...].
  counts(specs) {
    return onMain(() => {
      need(Array.isArray(specs) && specs.length <= 5000, 'counts: a list of up to 5000 count specs');
      const e = env();
      return specs.map(spec => count(e, spec));
    });
  },

  // spec: {target, selector, args, encoding?, fields?, save?, quiet?, read_only?}
  // A write is refused while the app holds unsaved changes. Once the business
  // method has run, what it left unsaved is saved (inside the same quiet
  // window) before its result is read, so a failure reading the result can
  // neither lose the write nor leave it pending.
  invoke(spec) {
    return onMain(() => {
      const e = env();
      if (spec.read_only) return answer(e, spec);
      const before = contextState(e);
      requireClean(e, before);
      captured = [];
      let after = null, saved;
      const called = quietly(spec, () => {
        const result = callOn(e, spec);
        after = contextState(e);
        if (spec.save && after.pending > 0) saved = save(e);
        return result;
      });
      const out = { context_before: before, context_after: after };
      if (saved) out.save = saved;
      const notices = takeNotices();
      if (notices.length) out.notices = notices;
      try {
        out.value = present(called, spec.fields);
      } catch (err) {
        fail(`MoneyWiz carried out ${spec.selector}${saved && saved.saved ? ' and saved it' : ''}, ` +
             `but its result could not be read (${err.message}); check the app before repeating it`);
      }
      return out;
    });
  },

  // Several calls in one main-queue turn; stops at the first failure.
  // Several calls in one main-queue turn, for a business object that takes
  // two calls (a report is created unsaved, then saved). A later call can name
  // an earlier one's object as {prev: i}, counting calls that returned an
  // object. Stops at the first failure and deletes what the batch inserted,
  // so nothing half-made is left for MoneyWiz's next save.
  batch(specs) {
    return onMain(() => {
      const e = env();
      requireClean(e, contextState(e));
      e.produced = [];
      const results = [];
      captured = [];
      try {
        for (const spec of specs) {
          if (spec.query) {
            const objects = fetchObjects(e, spec.query);
            results.push({ items: objects.map(o => serialize(o, spec.query.fields)) });
          } else results.push({ value: quietly(spec, () => invokeOn(e, spec)) });
        }
      } catch (error) {
        for (const o of e.produced)
          if (!isNil(o) && kindOf(o, C.NSManagedObject) && o.isInserted()) e.context.deleteObject_(o);
        throw error;
      }
      const out = { results, context_after: contextState(e) };
      if (specs.some(s => s.save) && out.context_after.pending > 0) out.save = save(e);
      const notices = takeNotices();
      if (notices.length) out.notices = notices;
      return out;
    });
  },

  save() { return onMain(() => save(env())); },

  // Attachments. MoneyWiz is sandboxed and cannot read the caller's files, so
  // the server sends the bytes and the agent writes them into MoneyWiz's own
  // temporary directory, where addImagesAtPaths:toTransaction: can copy them.
  stageFile(name, base64) {
    return onMain(() => {
      need(typeof name === 'string' && /^[A-Za-z0-9._-]{1,120}$/.test(name), 'stageFile: plain file name');
      need(typeof base64 === 'string' && base64.length <= MAX_ATTACHMENT * 4 / 3 + 4, 'stageFile: file too large');
      const fm = C.NSFileManager.defaultManager(), dir = stagingDir();
      fm.createDirectoryAtPath_withIntermediateDirectories_attributes_error_(nsString(dir), true, NULL, NULL);
      const data = C.NSData.alloc().initWithBase64EncodedString_options_(nsString(base64), 0);
      need(!isNil(data), 'stageFile: not base64');
      const path = `${dir}/${Date.now()}-${name}`;
      need(data.writeToFile_atomically_(nsString(path), true), 'stageFile: could not write into MoneyWiz\'s temporary directory');
      return path;
    });
  },

  unstageFile(path) {
    return onMain(() => {
      need(typeof path === 'string' && path.startsWith(stagingDir() + '/') && !path.includes('..'), 'unstageFile: not a staged file');
      return Boolean(C.NSFileManager.defaultManager().removeItemAtPath_error_(nsString(path), NULL));
    });
  },

  // Bytes of one attachment (images in sortOrder), from MoneyWiz's own copy.
  attachment(gid, index) {
    return onMain(() => {
      const e = env(), tx = lookup(e, 'Transaction', gid);
      need(Number.isSafeInteger(index) && index >= 0, 'attachment: index');
      const sorted = tx.images().allObjects().sortedArrayUsingDescriptors_(C.NSArray.arrayWithObject_(
        C.NSSortDescriptor.sortDescriptorWithKey_ascending_(nsString('sortOrder'), true)));
      need(index < Number(sorted.count()), `attachment: the transaction has ${Number(sorted.count())} attachments`);
      // imageData is a decoded UIImage; read the stored file itself.
      const image = sorted.objectAtIndex_(index), path = image.imageAbsolutePath();
      const data = isNil(path) ? null : C.NSData.dataWithContentsOfFile_(path);
      const out = { index, path: isNil(path) ? null : path.toString(), local: !isNil(data) };
      const asset = image.receiptAsset();
      if (!isNil(asset)) {
        for (const k of ['originalFileName', 'mimeType']) { const v = asset.valueForKey_(nsString(k)); if (!isNil(v)) out[k] = v.toString(); }
      }
      if (!isNil(data)) {
        need(Number(data.length()) <= MAX_ATTACHMENT, 'attachment: too large to transfer');
        out.size = Number(data.length());
        out.base64 = data.base64EncodedStringWithOptions_(0).toString();
      }
      return out;
    });
  },

  // Save MoneyWiz's own follow-up of one of our calls (it prices a new forex
  // position a moment after creating it, without saving), but only when every
  // object a save would write is one the caller names. Anything else pending
  // is left alone, as for any other write.
  settle(allow) {
    return onMain(() => {
      const e = env();
      need(Array.isArray(allow) && allow.every(a => Array.isArray(a) && a.length === 2), 'settle: [[entity, gid], ...]');
      const allowed = new Set(allow.map(([entityName, gid]) => `${entityName} ${gid}`));
      const objects = pendingObjects(e);
      if (objects.length === 0) return { saved: false, pending: 0 };
      const others = objects.filter(o => !allowed.has(`${o.entity().name()} ${ref(o).gid}`));
      if (others.length) return { saved: false, pending: objects.length, others: others.length };
      return save(e);
    });
  },

  // Figures of a saved report, computed the way MoneyWiz computes one it shows
  // (-[MoneyWizManager calculateDataForReportAsync:delegate:]): a temporary copy
  // of the report in a child context forked from the main one, with saving,
  // sync tracking and notifications off, and that context reset afterwards.
  // The calculation writes scratch state into its context (every account's
  // display mode; for forecasts, future occurrences of scheduled transactions
  // with their schedules advanced), so it must never run in the main context.
  // spec: {gid | create, calls: [[class, "- selector", encoding]], fields, root: {key: shape},
  //        shapes: {name: {fields, each: {key: [shape, limit]}}}, max_nodes, timeout}
  // A report that is not saved (spec.create: {selector, args, encoding}, one of
  // MoneyWiz's typed creators, which builds a report without calculating it) is
  // built in the main context as MoneyWiz's report editor builds one; its
  // settings are copied and it is deleted again within this main-queue turn,
  // so nothing of it is left for a save. Only the copy is calculated.
  reportData(spec) {
    return new Promise((resolve, reject) => ObjC.schedule(ObjC.mainQueue, () => {
      const held = [];
      const hold = o => { if (!isNil(o)) { o.retain(); held.push(o); } return o; };
      let timer = null;
      try {
        const e = env(), method = checkedCalls(spec.calls);
        let report, built = null;
        if (spec.create) {
          requireClean(e, contextState(e));
          e.produced = [];
          try {
            invokeOn(e, { target: 'manager', selector: spec.create.selector, args: spec.create.args,
                          encoding: spec.create.encoding });
          } catch (err) {
            for (const o of e.produced)
              if (!isNil(o) && kindOf(o, C.NSManagedObject) && o.isInserted()) e.context.deleteObject_(o);
            throw err;
          }
          built = e.produced.length && !isNil(e.produced[e.produced.length - 1]) ? e.produced[e.produced.length - 1] : null;
          report = built;
        } else {
          report = lookup(e, 'CustomReport', spec.gid);
          need(Boolean(toJS(report.valueForKey_(nsString('isReportSaved')))), 'Not a saved report');
        }
        let settings, filters, rest, flags, sorting;
        try {
          need(!isNil(report) && kindOf(report, C.CustomReport), 'MoneyWiz did not build the report');
          const value = key => report.valueForKey_(nsString(key));
          const int = key => { const v = value(key); return isNil(v) ? 0 : Number(v.intValue()); };
          const bool = key => { const v = value(key); return !isNil(v) && Boolean(v.boolValue()); };
          settings = [int('predefinedReportType'), int('mainDataType')];
          const stored = value('filtersArray');
          filters = C.NSArray.alloc().initWithArray_copyItems_(isNil(stored) ? C.NSArray.array() : stored, true);
          held.push(filters); // alloc/init: already ours
          const setUser = method('TransactionsFilter', '- setUser:');
          for (let i = 0; i < Number(filters.count()); i++) {
            const f = filters.objectAtIndex_(i), m = f.respondsToSelector_(ObjC.selector('setUser:')) ? f['- setUser:'] : null;
            need(m && m.types.replace(/[0-9]/g, '') === setUser.types.replace(/[0-9]/g, ''), `Report filter ${f.$className} changed`);
          }
          rest = [int('chartType'), int('chartOptions'), int('breakdownPeriod'), int('predefinedDatesPeriod'),
            hold(value('fromDate')), hold(value('toDate'))];
          flags = [bool('includeSheduledTransactions'), bool('includeArchivedAccounts')];
          sorting = hold(value('reportSortingType'));
        } finally {
          if (built !== null && kindOf(built, C.NSManagedObject) && built.isInserted()) {
            e.context.deleteObject_(built);
            e.context.processPendingChanges();
          }
        }
        if (built !== null) {
          const left = contextState(e);
          need(left.pending === 0, `Building the report left ${left.pending} unsaved changes in MoneyWiz`);
        }
        const userID = hold(e.user.objectID());
        const child = hold(method('MoneyWizManager', '- forkToChildManagedObjectContext').call(e.manager));
        need(!isNil(child), 'MoneyWiz did not fork a context for the report');
        const childContext = hold(method('DataAccessor', '- managedObjectContext')
          .call(method('MoneyWizManager', '- dataAccessor').call(child)));
        childContext.userInfo().setObject_forKey_(C.NSNumber.numberWithBool_(true), nsString('dontTrackSyncChanges'));
        method('MoneyWizManager', '- disableExternalNotifications').call(child);
        method('MoneyWizManager', '- disableDataSavingFromSource:').call(child, nsString('moneywiz-mcp reportData'));
        const create = method('MoneyWizManager', '- ' + REPORT_CREATE);
        const setSorting = method('CustomReport', '- setReportSortingType:');
        // On a timeout the caller gets an error, but nothing the block uses is released until the
        // block has run (it then skips the calculation); if it never runs, those objects leak.
        const state = { abandoned: false }, seconds = spec.timeout || 120;
        timer = setTimeout(() => {
          state.abandoned = true;
          reject(new Error(`MoneyWiz did not finish the report within ${seconds} s`));
        }, seconds * 1000);
        const block = new ObjC.Block({ retType: 'void', argTypes: [], implementation: () => {
          let out = null, error = null;
          try {
            if (!state.abandoned) {
              const user = childContext.objectWithID_(userID);
              for (let i = 0; i < Number(filters.count()); i++) filters.objectAtIndex_(i).setUser_(user);
              const temp = create.call(child, user, ...settings, filters, ...rest, true, NULL, ...flags, NULL);
              need(!isNil(temp), 'MoneyWiz did not compute the report');
              if (!isNil(sorting)) setSorting.call(temp, sorting);
              const budget = { nodes: spec.max_nodes || 50000 };
              out = {};
              for (const key of spec.fields || []) { const v = readKey(temp, key); if (v !== undefined) out[key] = toJS(v); }
              for (const [key, shape] of Object.entries(spec.root || {})) {
                const v = readKey(temp, key);
                if (v !== undefined) out[key] = shaped(v, spec.shapes, shape, null, budget);
              }
            }
          } catch (err) { error = err; } finally {
            try { childContext.reset(); } catch (err) { error = error || err; }
          }
          ObjC.schedule(ObjC.mainQueue, () => {
            clearTimeout(timer);
            pending.delete(block);
            held.forEach(o => o.release());
            if (error) reject(new Error(error.message || String(error))); else resolve(out);
          });
        } });
        pending.add(block);
        childContext.performBlock_(block);
      } catch (err) {
        if (timer !== null) clearTimeout(timer);
        held.forEach(o => o.release());
        reject(err);
      }
    }));
  },

  // Read-only introspection for scripts/check_build.py: what a new MoneyWiz
  // build offers for the methods, ivars and model the server relies on.
  // A backup made the way MoneyWiz's automatic backups make one
  // (MWBackupsManager manageBackups): MoneyWiz zips its database on a
  // background queue (the iCloud store for an iCloud ledger, as its own
  // shouldUseICloudBackup decides) and hands the zip to a completion block on
  // the main queue, which moves it into MoneyWiz's backups folder. Unlike the
  // automatic run, nothing is pruned: older backups in that folder stay.
  // spec: {calls: [[class, "- selector", encoding]], timeout}
  backup(spec) {
    return new Promise((resolve, reject) => ObjC.schedule(ObjC.mainQueue, () => {
      // What the completion block uses stays retained until the block has run, even
      // after a timeout has answered the caller (a freed object crashed MoneyWiz once).
      const held = [];
      const hold = o => { if (!isNil(o)) { o.retain(); held.push(o); } return o; };
      const release = () => { held.forEach(o => o.release()); held.length = 0; };
      const state = { answered: false };
      const answer = (error, value) => {
        if (state.answered) return;
        state.answered = true;
        if (error) reject(error); else resolve(value);
      };
      let timer = null, called = false;
      try {
        const e = env(), method = checkedCalls(spec.calls);
        const M = C.MWBackupsManager, backups = method('MWBackupsManager', '+ defaultManager').call(M);
        need(!isNil(backups), 'MoneyWiz has no backup manager');
        need(!method('MWBackupsManager', '- backupCreationInProgress').call(backups),
          'MoneyWiz is making a backup of its own right now; retry in a moment');
        const icloud = Boolean(method('MWBackupsManager', '- shouldUseICloudBackup').call(backups));
        const folder = hold(method('MWBackupsManager', '+ backupsFolderURL').call(M));
        need(!isNil(folder), 'MoneyWiz has no backups folder set');
        const create = method('MoneyWizManager', icloud ? '- createMoneyWizICloudBackupFile:' : '- createMoneyWizBackupFile:');
        const seconds = spec.timeout || 300;
        const block = new ObjC.Block({ retType: 'void', argTypes: ['object', 'object'], implementation: (zip, failure) => {
          try {
            if (!isNil(failure)) fail(`MoneyWiz could not make the backup: ${failure.localizedDescription()}`);
            need(!isNil(zip), 'MoneyWiz made no backup file');
            const fm = C.NSFileManager.defaultManager();
            const scoped = Boolean(folder.startAccessingSecurityScopedResource());
            try {
              fm.createDirectoryAtPath_withIntermediateDirectories_attributes_error_(folder.path(), true, NULL, NULL);
              const target = folder.URLByAppendingPathComponent_(zip.lastPathComponent());
              // A backup from the same minute has the same name; MoneyWiz replaces it too.
              if (fm.fileExistsAtPath_(target.path())) fm.removeItemAtURL_error_(target, NULL);
              need(fm.moveItemAtURL_toURL_error_(zip, target, NULL), 'Could not move the backup into MoneyWiz\'s backups folder');
              const attrs = fm.attributesOfItemAtPath_error_(target.path(), NULL);
              answer(null, { path: target.path().toString(), file_name: zip.lastPathComponent().toString(),
                             size: isNil(attrs) ? null : Number(attrs.fileSize()),
                             kind: icloud ? 'icloud_database' : 'data_folder' });
            } finally {
              if (scoped) folder.stopAccessingSecurityScopedResource();
            }
          } catch (err) {
            answer(new Error(err.message || String(err)));
          } finally {
            clearTimeout(timer);
            pending.delete(block);
            release();
          }
        } });
        timer = setTimeout(() => answer(new Error(`MoneyWiz did not finish the backup within ${seconds} s; `
          + 'it may still appear in its backups folder')), seconds * 1000);
        pending.add(block);
        called = true;
        create.call(e.manager, block);
      } catch (err) {
        if (timer !== null) clearTimeout(timer);
        if (!called) release();
        answer(err);
      }
    }));
  },

  signatures(pairs) {
    need(Array.isArray(pairs), 'signatures: [[class, "- selector"], ...]');
    return pairs.map(([className, selector]) => {
      const k = C[className];
      if (k === undefined) return null;
      const method = k[selector];
      return method === undefined ? null : method.types;
    });
  },

  hasIvar(className, name) {
    const k = C[className];
    if (k === undefined) return false;
    const getter = new NativeFunction(Module.getGlobalExportByName('class_getInstanceVariable'), 'pointer', ['pointer', 'pointer']);
    return !getter(k.handle, Memory.allocUtf8String(name)).isNull();
  },

  model() {
    return onMain(() => {
      const e = env(), entities = e.model.entities(), out = {};
      for (let i = 0; i < Number(entities.count()); i++) {
        const d = entities.objectAtIndex_(i), props = d.propertiesByName(), keys = props.allKeys(), properties = {};
        for (let j = 0; j < Number(keys.count()); j++) {
          const p = props.objectForKey_(keys.objectAtIndex_(j));
          properties[keys.objectAtIndex_(j).toString()] = kindOf(p, C.NSRelationshipDescription)
            ? { to: p.destinationEntity().name().toString(), many: Boolean(p.isToMany()) } : { type: Number(p.attributeType()) };
        }
        const sup = d.superentity(), subs = d.subentities(), sub = [];
        for (let j = 0; j < Number(subs.count()); j++) sub.push(subs.objectAtIndex_(j).name().toString());
        out[d.name().toString()] = { cls: d.managedObjectClassName().toString(), parent: isNil(sup) ? null : sup.name().toString(),
                                      children: sub, properties };
      }
      return out;
    });
  },

  methods(className, filter) {
    const k = C[className];
    need(k !== undefined, 'Unknown class ' + className);
    return k.$ownMethods.filter(m => !filter || m.toLowerCase().includes(filter.toLowerCase()))
      .map(m => [m, k[m].types]);
  },
};
