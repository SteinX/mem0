import type { InvokeModelCommand } from "@aws-sdk/client-bedrock-runtime";
import { AWSBedrockEmbedder } from "../embeddings/aws_bedrock";
import { EmbedderFactory } from "../utils/factory";

/**
 * Only the network boundary is faked: `BedrockRuntimeClient.send` never leaves
 * the process. `InvokeModelCommand` stays the real class from the AWS SDK, so
 * every assertion below runs against the exact payload Bedrock would receive.
 */
const mockSend = jest.fn();
const mockClientConfigs: any[] = [];

jest.mock("@aws-sdk/client-bedrock-runtime", () => {
  const actual = jest.requireActual("@aws-sdk/client-bedrock-runtime");
  return {
    ...actual,
    BedrockRuntimeClient: jest.fn().mockImplementation((config: unknown) => {
      mockClientConfigs.push(config);
      return { send: mockSend };
    }),
  };
});

const encode = (payload: unknown) => ({
  body: new TextEncoder().encode(JSON.stringify(payload)),
});

const titanReply = (embedding: number[]) =>
  encode({ embedding, inputTextTokenCount: embedding.length });

const cohereReply = (embeddings: number[][]) =>
  encode({ embeddings, id: "req-1", response_type: "embeddings_floats" });

const commandAt = (index: number): InvokeModelCommand =>
  mockSend.mock.calls[index][0];

const requestBodyAt = (index: number) =>
  JSON.parse(new TextDecoder().decode(commandAt(index).input.body));

describe("AWSBedrockEmbedder", () => {
  const savedEnv = { ...process.env };

  beforeEach(() => {
    jest.clearAllMocks();
    mockClientConfigs.length = 0;
    delete process.env.AWS_REGION;
  });

  afterAll(() => {
    process.env = savedEnv;
  });

  describe("Titan models", () => {
    it("sends inputText and returns the embedding vector", async () => {
      mockSend.mockResolvedValueOnce(titanReply([0.1, 0.2, 0.3]));
      const embedder = new AWSBedrockEmbedder({});

      const embedding = await embedder.embed("hello world");

      expect(embedding).toEqual([0.1, 0.2, 0.3]);
      expect(mockSend).toHaveBeenCalledTimes(1);
      expect(commandAt(0).input.modelId).toBe("amazon.titan-embed-text-v1");
      expect(commandAt(0).input.contentType).toBe("application/json");
      expect(commandAt(0).input.accept).toBe("application/json");
      expect(requestBodyAt(0)).toEqual({ inputText: "hello world" });
    });

    it("forwards dimensions to Titan V2 when embeddingDims is set", async () => {
      mockSend.mockResolvedValueOnce(titanReply([0.1, 0.2]));
      const embedder = new AWSBedrockEmbedder({
        model: "amazon.titan-embed-text-v2:0",
        embeddingDims: 512,
      });

      await embedder.embed("hello");

      expect(requestBodyAt(0)).toEqual({ inputText: "hello", dimensions: 512 });
    });

    it("omits dimensions on Titan V1, which rejects the field", async () => {
      mockSend.mockResolvedValueOnce(titanReply([0.1, 0.2]));
      const embedder = new AWSBedrockEmbedder({
        model: "amazon.titan-embed-text-v1",
        embeddingDims: 512,
      });

      await embedder.embed("hello");

      expect(requestBodyAt(0)).toEqual({ inputText: "hello" });
    });

    it("embedBatch issues one request per text and preserves order", async () => {
      mockSend
        .mockResolvedValueOnce(titanReply([1, 1]))
        .mockResolvedValueOnce(titanReply([2, 2]));
      const embedder = new AWSBedrockEmbedder({});

      const embeddings = await embedder.embedBatch(["first", "second"]);

      expect(embeddings).toEqual([
        [1, 1],
        [2, 2],
      ]);
      expect(mockSend).toHaveBeenCalledTimes(2);
      expect(requestBodyAt(0)).toEqual({ inputText: "first" });
      expect(requestBodyAt(1)).toEqual({ inputText: "second" });
    });
  });

  describe("Cohere models", () => {
    it("sends texts with a search_document input type", async () => {
      mockSend.mockResolvedValueOnce(cohereReply([[0.4, 0.5]]));
      const embedder = new AWSBedrockEmbedder({
        model: "cohere.embed-english-v3",
      });

      const embedding = await embedder.embed("hello");

      expect(embedding).toEqual([0.4, 0.5]);
      expect(requestBodyAt(0)).toEqual({
        texts: ["hello"],
        input_type: "search_document",
      });
    });

    it("embedBatch sends every text in a single request", async () => {
      mockSend.mockResolvedValueOnce(cohereReply([[1], [2], [3]]));
      const embedder = new AWSBedrockEmbedder({
        model: "cohere.embed-multilingual-v3",
      });

      const embeddings = await embedder.embedBatch(["a", "b", "c"]);

      expect(embeddings).toEqual([[1], [2], [3]]);
      expect(mockSend).toHaveBeenCalledTimes(1);
      expect(requestBodyAt(0).texts).toEqual(["a", "b", "c"]);
    });

    it("embedBatch splits requests at Cohere's 96 text limit", async () => {
      const texts = Array.from({ length: 100 }, (_, i) => `text-${i}`);
      mockSend
        .mockResolvedValueOnce(
          cohereReply(texts.slice(0, 96).map((_, i) => [i])),
        )
        .mockResolvedValueOnce(cohereReply(texts.slice(96).map((_, i) => [i])));
      const embedder = new AWSBedrockEmbedder({
        model: "cohere.embed-english-v3",
      });

      const embeddings = await embedder.embedBatch(texts);

      expect(embeddings).toHaveLength(100);
      expect(mockSend).toHaveBeenCalledTimes(2);
      expect(requestBodyAt(0).texts).toHaveLength(96);
      expect(requestBodyAt(1).texts).toEqual([
        "text-96",
        "text-97",
        "text-98",
        "text-99",
      ]);
    });
  });

  describe("client configuration", () => {
    it("defaults to the us-west-2 region", async () => {
      mockSend.mockResolvedValueOnce(titanReply([1]));
      await new AWSBedrockEmbedder({}).embed("hello");

      expect(mockClientConfigs[0].region).toBe("us-west-2");
    });

    it("prefers awsRegion over the AWS_REGION environment variable", async () => {
      process.env.AWS_REGION = "eu-central-1";
      mockSend.mockResolvedValueOnce(titanReply([1]));
      await new AWSBedrockEmbedder({ awsRegion: "ap-south-1" }).embed("hello");

      expect(mockClientConfigs[0].region).toBe("ap-south-1");
    });

    it("falls back to the AWS_REGION environment variable", async () => {
      process.env.AWS_REGION = "eu-central-1";
      mockSend.mockResolvedValueOnce(titanReply([1]));
      await new AWSBedrockEmbedder({}).embed("hello");

      expect(mockClientConfigs[0].region).toBe("eu-central-1");
    });

    it("passes explicitly configured credentials to the client", async () => {
      mockSend.mockResolvedValueOnce(titanReply([1]));
      await new AWSBedrockEmbedder({
        awsAccessKeyId: "AKIA_TEST",
        awsSecretAccessKey: "secret",
        awsSessionToken: "token",
      }).embed("hello");

      expect(mockClientConfigs[0].credentials).toEqual({
        accessKeyId: "AKIA_TEST",
        secretAccessKey: "secret",
        sessionToken: "token",
      });
    });

    it("leaves credentials unset so the AWS default credential chain applies", async () => {
      mockSend.mockResolvedValueOnce(titanReply([1]));
      await new AWSBedrockEmbedder({}).embed("hello");

      expect(mockClientConfigs[0].credentials).toBeUndefined();
    });

    it("rejects a half-configured credential pair", () => {
      expect(
        () => new AWSBedrockEmbedder({ awsAccessKeyId: "AKIA_TEST" }),
      ).toThrow(/awsAccessKeyId and awsSecretAccessKey/);
      expect(
        () => new AWSBedrockEmbedder({ awsSecretAccessKey: "secret" }),
      ).toThrow(/awsAccessKeyId and awsSecretAccessKey/);
    });

    // Silently ignoring a lone session token would fall back to the ambient
    // credential chain, embedding under an identity the caller never chose.
    it("rejects a session token supplied without the key pair", () => {
      expect(
        () => new AWSBedrockEmbedder({ awsSessionToken: "token" }),
      ).toThrow(/awsAccessKeyId and awsSecretAccessKey/);
    });
  });

  describe("provider registration", () => {
    it("is constructed by EmbedderFactory for the aws_bedrock provider", () => {
      const embedder = EmbedderFactory.create("aws_bedrock", {
        model: "amazon.titan-embed-text-v2:0",
      });

      expect(embedder).toBeInstanceOf(AWSBedrockEmbedder);
    });
  });

  describe("error handling", () => {
    it("wraps Bedrock failures with the model id", async () => {
      mockSend.mockRejectedValueOnce(new Error("AccessDeniedException"));
      const embedder = new AWSBedrockEmbedder({});

      await expect(embedder.embed("hello")).rejects.toThrow(
        "Error getting embedding from AWS Bedrock model amazon.titan-embed-text-v1: AccessDeniedException",
      );
    });

    it("fails when the response carries no embedding", async () => {
      mockSend.mockResolvedValueOnce(encode({ inputTextTokenCount: 3 }));
      const embedder = new AWSBedrockEmbedder({});

      await expect(embedder.embed("hello")).rejects.toThrow(
        /returned no embedding/,
      );
    });

    it("returns an empty array for an empty batch without calling Bedrock", async () => {
      const embedder = new AWSBedrockEmbedder({});

      await expect(embedder.embedBatch([])).resolves.toEqual([]);
      expect(mockSend).not.toHaveBeenCalled();
    });
  });
});
