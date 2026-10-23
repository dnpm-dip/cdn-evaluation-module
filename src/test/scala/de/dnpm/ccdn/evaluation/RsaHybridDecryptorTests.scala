package de.dnpm.ccdn.evaluation

import java.nio.charset.StandardCharsets.UTF_8
import java.nio.file.Files
import java.security.{KeyFactory, KeyPair}
import java.security.interfaces.RSAPrivateCrtKey
import java.security.spec.RSAPublicKeySpec
import scala.sys.process._
import scala.util.Try
import de.dnpm.ccdn.evaluation.collect.RsaHybridDecryptor
import org.scalatest.EitherValues
import org.scalatest.flatspec.AnyFlatSpec
import org.scalatest.matchers.should.Matchers
import play.api.libs.json.Json


class RsaHybridDecryptorTests extends AnyFlatSpec with Matchers with EitherValues
{
  import TestCrypto._

  private val payload = Json.obj("tan" -> "abc", "umlaut" -> "äöü")

  "RsaHybridDecryptor" should "decrypt what the central-data-node encrypted" in {
    val plain = defaultDecryptor.decrypt(encrypt(payload)).value
    Json.parse(new String(plain, UTF_8)) shouldBe payload
  }

  it should "report a key mismatch for content encrypted for another key pair" in {
    val failure = defaultDecryptor.decrypt(encrypt(payload, keyPair())).left.value
    failure.keyMismatch shouldBe true
  }

  it should "reject an unknown algorithm" in {
    val failure = defaultDecryptor.decrypt(encrypt(payload).copy(algorithm = "ROT13")).left.value
    failure.keyMismatch shouldBe false
  }

  it should "report corrupted content" in {
    val enc = encrypt(payload)
    val failure = defaultDecryptor.decrypt(enc.copy(ciphertext = enc.ciphertext.reverse)).left.value
    failure.keyMismatch shouldBe false
  }

  "Loading the private key" should "work for a passphrase-encrypted PKCS#8 key" in {
    val pem = encryptedPem(defaultKeyPair.getPrivate, "secret")
    RsaHybridDecryptor.loadPrivateKey(pem, Some("secret".toCharArray)).value shouldBe defaultKeyPair.getPrivate
  }

  it should "fail with a wrong or missing passphrase" in {
    val pem = encryptedPem(defaultKeyPair.getPrivate, "secret")
    RsaHybridDecryptor.loadPrivateKey(pem, Some("wrong".toCharArray)).left.value should include ("wrong passphrase")
    RsaHybridDecryptor.loadPrivateKey(pem, None).left.value should include ("no passphrase")
  }

  it should "work for an unencrypted PKCS#8 key, without asking for a passphrase" in {
    RsaHybridDecryptor.loadPrivateKey(plainPem(defaultKeyPair.getPrivate), fail("passphrase evaluated"))
      .value shouldBe defaultKeyPair.getPrivate
  }

  it should "reject other key formats" in {
    RsaHybridDecryptor.loadPrivateKey("-----BEGIN RSA PRIVATE KEY-----\nAAAA\n-----END RSA PRIVATE KEY-----", None)
      .left.value should include ("openssl pkcs8")
    RsaHybridDecryptor.loadPrivateKey("garbage", None).isLeft shouldBe true
  }

  it should "read a key generated as documented for the deployment (if openssl is installed)" in {
    assume(Try("openssl version".!!).isSuccess, "openssl not available")
    val dir = Files.createTempDirectory("evaluation-key")
    val key = dir.resolve("private.pem")
    try {
      Seq("openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048",
        "-aes256", "-pass", "pass:test-passphrase", "-out", key.toString).!! : Unit
      val privateKey =
        RsaHybridDecryptor.loadPrivateKey(Files.readString(key), Some("test-passphrase".toCharArray))
          .value.asInstanceOf[RSAPrivateCrtKey]
      val publicKey =
        KeyFactory.getInstance("RSA").generatePublic(new RSAPublicKeySpec(privateKey.getModulus, privateKey.getPublicExponent))
      val plain = new RsaHybridDecryptor(privateKey).decrypt(encrypt(payload, new KeyPair(publicKey, privateKey))).value
      Json.parse(new String(plain, UTF_8)) shouldBe payload
    } finally {
      Files.deleteIfExists(key)
      Files.deleteIfExists(dir) : Unit
    }
  }
}
