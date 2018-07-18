import argparse

import tensorflow as tf
import opennmt as onmt

import waveflow


def inf(src, tgt, src_vocab, tgt_vocab, direction, output_file):

  # Step 1
  def load_data(input_file, input_vocab):
    """Returns an iterator over the input file.

    Args:
      input_file: The input text file.
      input_vocab: The input vocabulary.

    Returns:
      A dataset batch iterator.
    """
    dataset = tf.data.TextLineDataset(input_file)
    dataset = dataset.map(lambda x: tf.string_split([x]).values)
    dataset = dataset.map(input_vocab.lookup)
    dataset = dataset.map(lambda x: {
        "ids": x,
        "length": tf.shape(x)[0]})
    dataset = dataset.padded_batch(64, {
        "ids": [None],
        "length": []})
    return dataset.make_initializable_iterator()

  if direction == 1:
    src_file, tgt_file = src, tgt
    src_vocab_file, tgt_vocab_file = src_vocab, tgt_vocab
  else:
    src_file, tgt_file = tgt, src
    src_vocab_file, tgt_vocab_file = tgt_vocab, src_vocab

  from opennmt.utils.misc import count_lines

  tgt_vocab_size = count_lines(tgt_vocab_file) + 1
  src_vocab_size = count_lines(src_vocab_file) + 1
  src_vocab = tf.contrib.lookup.index_table_from_file(
      src_vocab_file,
      vocab_size=src_vocab_size - 1,
      num_oov_buckets=1)

  with tf.device("cpu:0"):
    src_iterator = load_data(src_file, src_vocab)

  src = src_iterator.get_next()


  # Step 2


  hidden_size = 512
  encoder = onmt.encoders.BidirectionalRNNEncoder(2, hidden_size)
  decoder = onmt.decoders.AttentionalRNNDecoder(
      2, hidden_size, bridge=onmt.layers.CopyBridge())

  with tf.variable_scope("src" if direction == 1 else "tgt"):
    src_emb = tf.get_variable("embedding", shape=[src_vocab_size, 300])
    src_gen = tf.layers.Dense(src_vocab_size)
    src_gen.build([None, hidden_size])

  with tf.variable_scope("tgt" if direction == 1 else "src"):
    tgt_emb = tf.get_variable("embedding", shape=[tgt_vocab_size, 300])
    tgt_gen = tf.layers.Dense(tgt_vocab_size)
    tgt_gen.build([None, hidden_size])


  # Step 3


  from opennmt import constants

  def encode():
    """Encodes src.

    Returns:
      A tuple (encoder output, encoder state, sequence length).
    """
    with tf.variable_scope("encoder"):
      return encoder.encode(
          tf.nn.embedding_lookup(src_emb, src["ids"]),
          sequence_length=src["length"],
          mode=tf.estimator.ModeKeys.PREDICT)

  def decode(encoder_output):
    """Dynamically decodes from the encoder output.

    Args:
      encoder_output: The output of encode().

    Returns:
      A tuple with: the decoded word ids and the length of each decoded sequence.
    """
    batch_size = tf.shape(src["length"])[0]
    start_tokens = tf.fill([batch_size], constants.START_OF_SENTENCE_ID)
    end_token = constants.END_OF_SENTENCE_ID

    with tf.variable_scope("decoder"):
      sampled_ids, _, sampled_length, _ = decoder.dynamic_decode_and_search(
          tgt_emb,
          start_tokens,
          end_token,
          vocab_size=tgt_vocab_size,
          initial_state=encoder_output[1],
          beam_width=5,
          maximum_iterations=200,
          output_layer=tgt_gen,
          mode=tf.estimator.ModeKeys.PREDICT,
          memory=encoder_output[0],
          memory_sequence_length=encoder_output[2])
      return sampled_ids, sampled_length

  encoder_output = encode()
  sampled_ids, sampled_length = decode(encoder_output)

  tgt_vocab_rev = tf.contrib.lookup.index_to_string_table_from_file(
    tgt_vocab_file,
    vocab_size=tgt_vocab_size - 1,
    default_value=constants.UNKNOWN_TOKEN)

  tokens = tgt_vocab_rev.lookup(tf.cast(sampled_ids, tf.int64))
  length = sampled_length


  # Step 4


  from opennmt.utils.misc import print_bytes

  saver = tf.train.Saver()
  checkpoint_path = tf.train.latest_checkpoint("model")

  def session_init_op(_scaffold, sess):
    saver.restore(sess, checkpoint_path)
    tf.logging.info("Restored model from %s", checkpoint_path)

  scaffold = tf.train.Scaffold(init_fn=session_init_op)
  session_creator = tf.train.ChiefSessionCreator(scaffold=scaffold)
  
  
  f = open(output_file, 'a')

  with tf.train.MonitoredSession(session_creator=session_creator) as sess:

    sess.run(src_iterator.initializer)
    while not sess.should_stop():
      _tokens, _length = sess.run([tokens, length])
      for b in range(_tokens.shape[0]):
        pred_toks = _tokens[b][0][:_length[b][0] - 1]
        pred_sent = b" ".join(pred_toks)
        print_bytes(pred_sent, f)
  
  f.close()
