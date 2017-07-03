import logging
import random
import itertools
import numpy as np
import pandas as pd
import tensorflow as tf

from insta_pb2 import User
import features
from features import FEATURES, NFEATS
from constants import NONE_PRODUCTID

class Batcher(object):
  def __init__(self, hps, recordpath):
    self.hps = hps
    self.recordpath = recordpath
    self.batch_size = hps.batch_size
    self.nfeats = hps.nfeats
    if self.nfeats != NFEATS:
      # We're not using all the crayons in the crayon box. Either we've deliberately
      # chosen not to use some features, or we're running in 'legacy mode' (i.e. we've
      # added more features since we trained this model).
      feats = features.lookup_features(hp.feats)
      assert sum(f.arity for f in feats) == hhps.nfeats
      self.feat_fixture = (feats, hps.nfeats)
    else:
      self.feat_fixture = None
    self.max_seq_len = hps.max_seq_len
    self.reset_record_iterator()

  def reset_record_iterator(self):
    self.records = tf.python_io.tf_record_iterator(self.recordpath)

  def get_batches(self, pids_per_user=1, infinite=True, allow_smaller_final_batch=False):
    """if pids_per_user == -1, then use all pids
    TODO: should maybe yield dicts rather than massive tuples
    """
    bs = self.batch_size
    maxlen = self.max_seq_len
    # XXX: is it okay to overwrite and re-yield these ndarrays?
    # proooobably? callers should never modify their values, and
    # after they're ready for the next() batch, they should have
    # no reason to be interacting with the previous one. But gotta
    # be careful.
    x = np.zeros([bs, maxlen, self.nfeats])
    labels = np.zeros([bs, maxlen])
    seqlens = np.zeros([bs], dtype=np.int32)
    lossmask = np.zeros([bs, maxlen])
    # (These are actually pids minus one. Kaggle pids start from 1, and we want
    # our indices to start from 0.)
    pids = np.zeros([bs], dtype=np.int32)
    uids = np.zeros([bs], dtype=np.int32)

    # TODO: Not clear if it's necessary to call this between batches.
    # Ideally we'd be smart enough not to care about any junk beyond :seqlen
    # in any of the returned arrays, but not sure that's the case.
    def _clear_batch_arrays():
      # Only need to do this for arrays with values per sequence element,
      # rather than just per sequence
      for arr in [x, labels, lossmask]:
        arr[:] = 0

    i = 0 # index into current batch
    while 1:
      user = User()
      try:
        user.ParseFromString(self.records.next())
      except StopIteration:
        if infinite:
          logging.info("Starting a new 'epoch'. Resetting record iterator.")
          self.reset_record_iterator()
          user.ParseFromString(self.records.next())
        else:
          if allow_smaller_final_batch and i > 0:
            # Set remaining uid/pid/seqlen slots to 0 as signal to caller that
            # these are just dummy/padding values
            for arr in [pids, uids, seqlens]:
              arr[i:] = 0
            yield x, labels, seqlens, lossmask, pids, uids
          # (Not clear if we should do this as a matter of course, or leave it up to caller)
          self.reset_record_iterator()
          raise StopIteration
      wrapper = UserWrapper(user, self.feat_fixture)
      if pids_per_user == 1:
        user_pids = [wrapper.sample_pid()]
      elif pids_per_user == -1:
        user_pids = wrapper.all_pids
      else:
        assert False, "not implemented"
      for pid in user_pids:
        ts = wrapper.training_sequence_for_pid(pid, maxlen)
        x[i] = ts['x']
        labels[i] = ts['labels']
        seqlens[i] = ts['seqlen']
        lossmask[i] = ts['lossmask']
        pids[i] = ts['pindex']
        uids[i] = user.uid
        i += 1
        if i == bs:
          yield x, labels, seqlens, lossmask, pids, uids
          i = 0
          _clear_batch_arrays()
          

def iterate_wrapped_users(recordpath):
  records = tf.python_io.tf_record_iterator(recordpath)
  for record in records:
    user = User()
    user.ParseFromString(record)
    yield UserWrapper(user)

class UserWrapper(object):
  """Wrapper around User protobuf objs.
  """
  # 3 for this order, 3 for prev order
  # (day, hour, days_since) + (prev_ordered, prev_nprods, prev_nreorders)
  NFEATS = 3 + 3 

  def __init__(self, user, feat_fixture=None):
    self.user = user
    self._all_pids = None
    self.feat_fixture = feat_fixture

  @property
  def uid(self):
    return self.user.uid

  @property
  def norders(self):
    return len(self.user.orders)

  @property
  def seqlen(self):
    return len(self.user.orders) - 1

  @property
  def all_pids(self):
    # This can get called a fair number of times, so cache it
    if self._all_pids is None:
      pids = set()
      for order in self.user.orders[:-1]:
        pids.update( set(order.products) )
      self._all_pids = pids
    return self._all_pids

  # TODO: should filter new products from last orders in the pb generation
  # step. then this won't be necessary.
  def last_order_predictable_prods(self):
    last_o = self.user.orders[-1]
    pids = self.all_pids
    last_pids = set(last_o.products)
    res = pids.intersection(last_pids)
    # Special case: if predictable prods is empty, return a single dummy
    # product id representing "none" (to be consistent with kaggle's scoring method)
    if not res:
      return set([NONE_PRODUCTID])
    return res

  # TODO: maybe these training_sequence methods should just return a 
  # big dataframe? This would solve the problem of having to return
  # these big tuples, and give us something for some downstream 
  # thing to work with for scaling or otherwise transforming features.
  # Also, for testing, it would make it easy to refer to features by name.
  # NB: not actually used right now
  def all_training_sequences(self, maxlen):
    pids = self.all_pids
    nseqs = len(pids)
    x = np.zeros([nseqs, maxlen, self.NFEATS])
    labels = np.zeros([nseqs, maxlen])
    lossmask = np.zeros([nseqs, maxlen])
    seqlens = np.zeros(nseqs) + self.seqlen
    for i, pid in enumerate(pids):
      # TODO: could be optimized to avoid redundant work
      ts = self.training_sequence_for_pid(pid, maxlen)
      x[i] = ts['x']
      labels[i] = ts['labels']
      lossmask[i] = ts['lossmask']
    return x, labels, seqlens, lossmask


  def sample_pid(self):
    uniform = 1
    if uniform:
      pids = list(self.all_pids)
      return random.choice(pids)
    # Sample an order, then a product. This gives more probability weight to
    # frequently ordered products. Not clear whether that's a good thing.
    # Why -2? We don't want to include the last order, just because there might
    # be pids there for products never ordered in any previous order. A train
    # sequence focused on that product will give no signal.
    iorder = random.randint(0, self.norders-2)
    order = self.user.orders[iorder]
    iprod = random.randint(0, len(order.products)-1)
    return order.products[iprod]

  def sample_training_sequence(self, maxlen):
    pid = self.sample_pid()
    return self.training_sequence_for_pid(pid, maxlen)

  rawcols = ['dow', 'hour', 'days_since_prior',
      'previously_ordered', 'n_prev_products', 'n_prev_repeats', 'n_prev_reorders']
  def rawfeats_to_df(self, featdata):
    return pd.DataFrame(featdata, columns=self.rawcols)
  def transform_raw_feats(self, featdata, maxlen): 
    df = self.rawfeats_to_df(featdata)
    if self.feat_fixture:
      feats, dimen = self.feat_fixture
      return vectorize(df, self.user, maxlen, feats, dimen)
    else:
      return vectorize(df, self.user, maxlen)

  def training_sequence_for_pid(self, pid, maxlen):
    """Return a tuple of (x, labels, seqlen, lossmask, pid)
    Where feats is an ndarray, seqlen and pid are scalars, and everything else is a 1-d array
    """
    x = np.zeros([self.seqlen, len(self.rawcols)])
    labels = np.zeros([maxlen])
    lossmask = np.zeros([maxlen])
    # Start from second order (because of reasons)
    prevs, orders = itertools.tee(self.user.orders)
    orders.next()
    seen_first = False
    prevprev = None # The order before last
    # ids of products seen up to prev order (but not including products in prev
    # order itself)
    pids_seen = set()
    for i, prev, order in itertools.izip(range(maxlen), prevs, orders):
      ordered = pid in order.products
      previously_ordered = int(pid in prev.products)
      labels[i] = int(ordered)
      seen_first = seen_first or previously_ordered
      # We only care about our ability to predict when a product is *re*ordered. 
      # So we zero out the loss for predicting all labels up to and including the
      # first order that has that product. (We also zero out the loss past the end
      # of the actual sequence, i.e. for the padding section)
      lossmask[i] = int(seen_first)
      prevprods = set(prev.products)
      if prevprev is None:
        prev_repeats = 0
      else:
        prod2 = set(prevprev.products)
        prev_repeats = len(prevprods.intersection(prod2))
      prev_reorders = len(prevprods.intersection(pids_seen))
      x[i] = (
          [order.dow, order.hour, order.days_since_prior]
        + [previously_ordered, len(prev.products), prev_repeats, prev_reorders]
      )
      prevprev = prev
      pids_seen.update(set(prev.products))

    feats = self.transform_raw_feats(x, maxlen)
    return dict(x=feats, labels=labels, seqlen=self.seqlen, lossmask=lossmask,
        pindex=pid-1, xraw=x
    )

def vectorize(df, user, maxlen, features=None, nfeats=None):
  features = features or FEATURES # Default to all of them
  nfeats = nfeats or NFEATS
  res = np.zeros([maxlen, nfeats])
  i = 0
  seqlen = len(df) 
  for feat in features:
    featvals = feat.fn(df, user)
    if feat.arity == 1:
      res[:seqlen,i] = featvals
    else:
      res[:seqlen,i:i+feat.arity] = featvals
    i += feat.arity
  return res
