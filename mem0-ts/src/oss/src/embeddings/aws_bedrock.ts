import { Embedder } from "./base";
import { EmbeddingConfig } from "../types";

const DEFAULT_MODEL = "amazon.titan-embed-text-v1";
const DEFAULT_REGION = "us-west-2";

// Cohere's Bedrock embed API rejects an InvokeModel call carrying more than 96
// texts, so `embedBatch` chunks at that boundary.
const COHERE_MAX_BATCH = 96;

type BedrockRuntimeModule = typeof import("@aws-sdk/client-bedrock-runtime");

interface BedrockCredentials {
  accessKeyId: string;
  secretAccessKey: string;
  sessionToken?: string;
}

interface BedrockEmbeddingResponse {
  // Titan returns a single vector, Cohere returns one vector per input text.
  embedding?: number[];
  embeddings?: number[][];
}

/**
 * AWS Bedrock embedder, mirroring `mem0/embeddings/aws_bedrock.py`.
 *
 * Supports the Amazon Titan and Cohere embedding model families. The
 * `@aws-sdk/client-bedrock-runtime` dependency is lazily imported so the
 * package stays optional: importing this module never forces the SDK to be
 * installed until a Bedrock embedder actually embeds something.
 */
export class AWSBedrockEmbedder implements Embedder {
  private readonly model: string;
  private readonly region: string;
  private readonly embeddingDims?: number;
  private readonly credentials?: BedrockCredentials;
  private clientPromise?: Promise<{
    sdk: BedrockRuntimeModule;
    client: { send: (command: any) => Promise<{ body?: Uint8Array }> };
  }>;

  constructor(config: EmbeddingConfig) {
    this.model = config.model || DEFAULT_MODEL;
    this.region = config.awsRegion || process.env.AWS_REGION || DEFAULT_REGION;
    this.embeddingDims = config.embeddingDims;

    const hasKeyPair = Boolean(
      config.awsAccessKeyId && config.awsSecretAccessKey,
    );
    const hasAnyCredential = Boolean(
      config.awsAccessKeyId ||
      config.awsSecretAccessKey ||
      config.awsSessionToken,
    );

    // Partially configured credentials would silently fall back to the default
    // chain, embedding under an identity the caller never chose.
    if (hasAnyCredential && !hasKeyPair) {
      throw new Error(
        "AWS Bedrock requires both awsAccessKeyId and awsSecretAccessKey when any explicit credential is configured. " +
          "Omit all credential fields to use the AWS default credential chain.",
      );
    }

    // Leaving `credentials` unset lets the AWS SDK resolve them from its
    // default chain: environment, shared config, SSO, or the instance role.
    if (hasKeyPair) {
      this.credentials = {
        accessKeyId: config.awsAccessKeyId!,
        secretAccessKey: config.awsSecretAccessKey!,
        ...(config.awsSessionToken && { sessionToken: config.awsSessionToken }),
      };
    }
  }

  private async loadSdk(): Promise<BedrockRuntimeModule> {
    try {
      return await import("@aws-sdk/client-bedrock-runtime");
    } catch (_) {
      throw new Error(
        "The '@aws-sdk/client-bedrock-runtime' package is required to use the AWS Bedrock embedder. " +
          "Install it with: npm install @aws-sdk/client-bedrock-runtime",
      );
    }
  }

  private getClient() {
    if (!this.clientPromise) {
      this.clientPromise = this.loadSdk().then((sdk) => ({
        sdk,
        client: new sdk.BedrockRuntimeClient({
          region: this.region,
          ...(this.credentials && { credentials: this.credentials }),
        }),
      }));
    }
    return this.clientPromise;
  }

  private isCohereModel(): boolean {
    return this.model.startsWith("cohere.");
  }

  private buildRequestBody(texts: string[]): Record<string, unknown> {
    if (this.isCohereModel()) {
      return { texts, input_type: "search_document" };
    }

    // Titan accepts one text per call. Only Titan Text Embeddings V2 supports
    // a caller-chosen output size (256/512/1024), so the field is guarded the
    // same way the Python provider guards it.
    return {
      inputText: texts[0],
      ...(this.embeddingDims !== undefined &&
        this.model.includes("v2") && { dimensions: this.embeddingDims }),
    };
  }

  private async invoke(texts: string[]): Promise<number[][]> {
    const { sdk, client } = await this.getClient();

    let payload: BedrockEmbeddingResponse;
    try {
      const response = await client.send(
        new sdk.InvokeModelCommand({
          modelId: this.model,
          contentType: "application/json",
          accept: "application/json",
          body: new TextEncoder().encode(
            JSON.stringify(this.buildRequestBody(texts)),
          ),
        }),
      );
      payload = JSON.parse(new TextDecoder().decode(response.body));
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      throw new Error(
        `Error getting embedding from AWS Bedrock model ${this.model}: ${message}`,
      );
    }

    // Validated outside the try so this message is not re-wrapped by the catch.
    const embeddings = this.isCohereModel()
      ? payload.embeddings
      : payload.embedding && [payload.embedding];

    if (!embeddings || embeddings.length !== texts.length) {
      throw new Error(
        `AWS Bedrock model ${this.model} returned no embedding for one or more inputs`,
      );
    }
    return embeddings;
  }

  async embed(text: string): Promise<number[]> {
    return (await this.invoke([text]))[0];
  }

  async embedBatch(texts: string[]): Promise<number[][]> {
    if (texts.length === 0) return [];

    if (!this.isCohereModel()) {
      return Promise.all(texts.map((text) => this.embed(text)));
    }

    const embeddings: number[][] = [];
    for (let i = 0; i < texts.length; i += COHERE_MAX_BATCH) {
      embeddings.push(
        ...(await this.invoke(texts.slice(i, i + COHERE_MAX_BATCH))),
      );
    }
    return embeddings;
  }
}
