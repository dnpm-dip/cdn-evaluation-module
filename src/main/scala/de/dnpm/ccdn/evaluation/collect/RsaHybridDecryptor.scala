package de.dnpm.ccdn.evaluation.collect

import java.security.spec.{MGF1ParameterSpec, PKCS8EncodedKeySpec}
import java.security.{KeyFactory, PrivateKey}
import java.util.Base64
import javax.crypto.spec.{IvParameterSpec, OAEPParameterSpec, PBEKeySpec, PSource, SecretKeySpec}
import javax.crypto.{Cipher, EncryptedPrivateKeyInfo, SecretKeyFactory}
import scala.util.Try


object RsaHybridDecryptor
{
  /** Name of the scheme in field "algorithm" of the encrypted content */
  val Algorithm = "RSA-OAEP-SHA256+AES-256-CBC"

  private val EncryptedPemType = "ENCRYPTED PRIVATE KEY"
  private val PlainPemType     = "PRIVATE KEY"

  /** (type, base64 body) of the first PEM block in `pem`, e.g. ("PRIVATE KEY", "MIIE...") */
  private def pemBlock(pem: String): Option[(String, String)] =
    "(?s)-----BEGIN ([A-Z ]+)-----(.*?)-----END \\1-----".r
      .findFirstMatchIn(pem)
      .map(m => (m.group(1), m.group(2).replaceAll("\\s", "")))

  /**
   * Reads an RSA private key in PKCS#8 PEM format, i.e. what `openssl genpkey` produces:
   * encrypted with a passphrase ("ENCRYPTED PRIVATE KEY", with `-aes256`, PBES2) or not ("PRIVATE KEY").
   *
   * @param passphrase required for an encrypted key, ignored otherwise
   */
  def loadPrivateKey(pem: String, passphrase: => Option[Array[Char]]): Either[String, PrivateKey] =
    pemBlock(pem) match {
      case Some((EncryptedPemType, body)) =>
        passphrase.toRight("The private key is encrypted, but no passphrase was given")
          .flatMap(pass =>
            Try(decryptPkcs8(Base64.getDecoder.decode(body), pass))
              .toEither.left.map(_ => "Couldn't decrypt the private key: wrong passphrase, or unsupported key encryption")
          )
          .flatMap(rsaKey)
      case Some((PlainPemType, body)) =>
        Try(new PKCS8EncodedKeySpec(Base64.getDecoder.decode(body)))
          .toEither.left.map(e => s"Invalid private key: ${e.getMessage}")
          .flatMap(rsaKey)
      case Some((other, _)) =>
        Left(
          s"Unsupported key format '$other', expected PKCS#8 ('$EncryptedPemType' or '$PlainPemType'); " +
          "convert it with: openssl pkcs8 -topk8 -v2 aes256 -in old.pem -out private.pem"
        )
      case None =>
        Left("No PEM-encoded private key found")
    }

  /**
   * Decrypts an EncryptedPrivateKeyInfo. Its algorithm is PBES2 (OID 1.2.840.113549.1.5.13) for
   * keys encrypted by OpenSSL, which the JCE can't instantiate by that name; the parameters'
   * toString yields the actual scheme instead, e.g. "PBEWithHmacSHA256AndAES_256".
   */
  private def decryptPkcs8(der: Array[Byte], passphrase: Array[Char]): PKCS8EncodedKeySpec = {
    val info   = new EncryptedPrivateKeyInfo(der)
    val params = info.getAlgParameters
    val scheme = Option(params).map(_.toString).getOrElse(info.getAlgName)
    val key    = SecretKeyFactory.getInstance(scheme).generateSecret(new PBEKeySpec(passphrase))
    val cipher = Cipher.getInstance(scheme)
    cipher.init(Cipher.DECRYPT_MODE, key, params)
    info.getKeySpec(cipher)
  }

  private def rsaKey(spec: PKCS8EncodedKeySpec): Either[String, PrivateKey] =
    Try(KeyFactory.getInstance("RSA").generatePrivate(spec))
      .toEither.left.map(e => s"Not an RSA private key: ${e.getMessage}")

  def fromPem(pem: String, passphrase: => Option[Array[Char]]): Either[String, RsaHybridDecryptor] =
    loadPrivateKey(pem, passphrase).map(new RsaHybridDecryptor(_))

  /**
   * @param keyMismatch true if the AES key couldn't be decrypted, i.e. probably the wrong private key is used
   */
  final case class Failure(message: String, keyMismatch: Boolean)
}


/**
 * Decrypts the content of backup documents, encrypted by the central-data-node's RsaHybridEncryptor:
 * the AES-256 key is encrypted with RSA-OAEP (SHA-256 for both OAEP and MGF1), the payload
 * with that key in AES-256-CBC with PKCS#5 padding.
 */
final class RsaHybridDecryptor(privateKey: PrivateKey)
{
  import RsaHybridDecryptor.Algorithm

  private val OaepParams =
    new OAEPParameterSpec("SHA-256", "MGF1", MGF1ParameterSpec.SHA256, PSource.PSpecified.DEFAULT)

  /**
   * The decrypted payload, or a description of the problem. A failure to decrypt the AES key
   * most likely means that the content was encrypted for another key pair.
   */
  def decrypt(content: BackupDocument.Encrypted): Either[RsaHybridDecryptor.Failure, Array[Byte]] = {
    import RsaHybridDecryptor.Failure
    val decoder = Base64.getDecoder
    for {
      _      <- Either.cond(content.algorithm == Algorithm, (), Failure(s"unsupported algorithm '${content.algorithm}'", keyMismatch = false))
      aesKey <- Try {
                  val rsa = Cipher.getInstance("RSA/ECB/OAEPPadding")
                  rsa.init(Cipher.DECRYPT_MODE, privateKey, OaepParams)
                  rsa.doFinal(decoder.decode(content.encryptedKey))
                }
                .toEither.left.map(_ => Failure("couldn't decrypt the AES key, the content is probably encrypted for another key pair", keyMismatch = true))
      plain  <- Try {
                  val aes = Cipher.getInstance("AES/CBC/PKCS5Padding")
                  aes.init(Cipher.DECRYPT_MODE, new SecretKeySpec(aesKey, "AES"), new IvParameterSpec(decoder.decode(content.iv)))
                  aes.doFinal(decoder.decode(content.ciphertext))
                }
                .toEither.left.map(e => Failure(s"couldn't decrypt the content: ${e.getMessage}", keyMismatch = false))
    } yield plain
  }
}
