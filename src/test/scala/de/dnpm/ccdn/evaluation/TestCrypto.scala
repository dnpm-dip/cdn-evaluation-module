package de.dnpm.ccdn.evaluation

import java.nio.charset.StandardCharsets.UTF_8
import java.security.spec.MGF1ParameterSpec
import java.security.{AlgorithmParameters, KeyPair, KeyPairGenerator, PrivateKey, SecureRandom}
import java.util.Base64
import javax.crypto.spec.{IvParameterSpec, OAEPParameterSpec, PBEKeySpec, PBEParameterSpec, PSource}
import javax.crypto.{Cipher, EncryptedPrivateKeyInfo, KeyGenerator, SecretKeyFactory}
import de.dnpm.ccdn.evaluation.collect.{BackupDocument, RsaHybridDecryptor}
import play.api.libs.json.{JsValue, Json}


object TestCrypto
{
  private val random = new SecureRandom

  /** 2048 bit (instead of 4096 in production) for faster tests */
  def keyPair(): KeyPair = {
    val gen = KeyPairGenerator.getInstance("RSA")
    gen.initialize(2048)
    gen.generateKeyPair()
  }

  lazy val defaultKeyPair: KeyPair = keyPair()

  /** Same as RsaHybridEncryptor.encrypt in the central-data-node */
  def encrypt(payload: JsValue, keys: KeyPair = defaultKeyPair): BackupDocument.Encrypted = {
    val keyGen = KeyGenerator.getInstance("AES")
    keyGen.init(256, random)
    val aesKey = keyGen.generateKey()
    val iv = new Array[Byte](16)
    random.nextBytes(iv)

    val aes = Cipher.getInstance("AES/CBC/PKCS5Padding")
    aes.init(Cipher.ENCRYPT_MODE, aesKey, new IvParameterSpec(iv))
    val ciphertext = aes.doFinal(Json.stringify(payload).getBytes(UTF_8))

    val rsa = Cipher.getInstance("RSA/ECB/OAEPPadding")
    rsa.init(Cipher.ENCRYPT_MODE, keys.getPublic,
      new OAEPParameterSpec("SHA-256", "MGF1", MGF1ParameterSpec.SHA256, PSource.PSpecified.DEFAULT))

    val b64 = Base64.getEncoder
    BackupDocument.Encrypted(
      RsaHybridDecryptor.Algorithm,
      b64.encodeToString(rsa.doFinal(aesKey.getEncoded)),
      b64.encodeToString(iv),
      b64.encodeToString(ciphertext)
    )
  }

  private def pem(pemType: String, der: Array[Byte]): String =
    s"-----BEGIN $pemType-----\n${Base64.getMimeEncoder(64, "\n".getBytes(UTF_8)).encodeToString(der)}\n-----END $pemType-----\n"

  def plainPem(key: PrivateKey): String =
    pem("PRIVATE KEY", key.getEncoded)

  /** PBES2 encrypted PKCS#8, like `openssl genpkey -aes256` produces */
  def encryptedPem(key: PrivateKey, passphrase: String): String = {
    val scheme = "PBEWithHmacSHA256AndAES_256"
    val salt = new Array[Byte](16)
    val iv = new Array[Byte](16)
    random.nextBytes(salt)
    random.nextBytes(iv)
    val secret = SecretKeyFactory.getInstance(scheme).generateSecret(new PBEKeySpec(passphrase.toCharArray))
    val cipher = Cipher.getInstance(scheme)
    cipher.init(Cipher.ENCRYPT_MODE, secret, new PBEParameterSpec(salt, 2048, new IvParameterSpec(iv)))
    // re-read as "PBES2", the name of the OID which EncryptedPrivateKeyInfo encodes
    val params = AlgorithmParameters.getInstance("PBES2")
    params.init(cipher.getParameters.getEncoded)
    pem("ENCRYPTED PRIVATE KEY", new EncryptedPrivateKeyInfo(params, cipher.doFinal(key.getEncoded)).getEncoded)
  }

  lazy val defaultDecryptor: RsaHybridDecryptor =
    new RsaHybridDecryptor(defaultKeyPair.getPrivate)
}
