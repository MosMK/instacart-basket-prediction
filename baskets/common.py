"""A bunch of helpers, so far all having to do with finding the paths for things.
"""
import os
import pickle
import tensorflow as tf

_thisdir = os.path.dirname(__file__)
ROOT = os.path.realpath(os.path.join(_thisdir, '..'))

DATA_DIR = os.path.join(ROOT, 'dat')
PDICT_DIR = os.path.join(DATA_DIR, 'pdicts')
USER_PB_DIR = os.path.join(DATA_DIR, 'user_pbs')
VECTOR_DIR = os.path.join(DATA_DIR, 'vectors')
SCALARVECTOR_DIR = os.path.join(DATA_DIR, 'scalar_vectors')

CONFIG_DIR = os.path.join(ROOT, 'configs')

LOG_DIR = os.path.join(ROOT, 'logs')

CHECKPOINT_DIR = os.path.join(ROOT, 'checkpoints')

XGBOOST_DIR = os.path.join(ROOT, 'nonrecurrent')
XGBOOST_MODEL_DIR = os.path.join(XGBOOST_DIR, 'models')

# Other options are ZLIB and NONE (though in the latter case, the name should be '', cause tf.contrib.data is quirky like that)
VECTOR_COMPRESSION_NAME = 'GZIP'
VECTOR_COMPRESSION_TYPE = getattr(tf.python_io.TFRecordCompressionType, 
    VECTOR_COMPRESSION_NAME or 'NONE')

def _resolve(identifier, extension, subdir):
  if '/' in identifier:
    # Hope you know what you're doing
    return identifier
  if not identifier.endswith(extension):
    identifier += extension
  return os.path.join(subdir, identifier)

def resolve_recordpath(recordpath):
  return _resolve(recordpath, '.tfrecords', USER_PB_DIR)

def resolve_vector_recordpath(recordpath):
  return _resolve(recordpath, '.tfrecords', VECTOR_DIR)

def resolve_scalarvector_path(identifier):
  return _resolve(identifier, '.npy', SCALARVECTOR_DIR)

def resolve_xgboostmodel_path(identifier):
  return _resolve(identifier, '.model', XGBOOST_MODEL_DIR)

def logdir_for_tag(tag):
  return os.path.join(LOG_DIR, tag)

def csv_path(basename):
  if not basename.endswith('.csv'):
    basename += '.csv'
  return os.path.join(DATA_DIR, 'csv', basename)

# Helpers for loading 'pdicts': mappings from uid to pid to predicted probability
# generated by some model for some set of users.
def pdict_for_tag(tag, recordfile):
  path = _path_for_pdict(tag, recordfile)
  with open(path) as f:
    return pickle.load(f)

def save_pdict_for_tag(tag, pdict, recordfile):
  path = _path_for_pdict(tag, recordfile)
  with open(path, 'w') as f:
    pickle.dump(dict(pdict), f)

def _path_for_pdict(tag, recordfile):
  ext = '.tfrecords'
  if recordfile.endswith(ext):
    recordfile = recordfile[:-len(ext)]
  assert '/' not in recordfile
  # 'test' is our test stratum, and the one we most commonly generate
  # predictions for.
  if recordfile != 'test':
    tag += '_{}'.format(recordfile)
  pickle_basename = '{}.pickle'.format(tag)
  return os.path.join(PDICT_DIR, pickle_basename)
