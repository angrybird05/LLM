import { z } from 'zod';

export function zodToJsonSchema(schema: z.ZodSchema<any>): any {
  // Use the public parse method to get the schema shape
  try {
    // For Zod v3+, we can use the _def property but need to cast
    const def = (schema as any)._def;
    
    if (!def) return { type: 'string' };
    
    // Handle different Zod types by checking the constructor name or typeName
    const typeName = def.typeName || def.constructor?.name || '';
    
    switch (typeName) {
      case 'ZodString': {
        const result: any = { type: 'string' };
        if (def.checks) {
          for (const check of def.checks) {
            if (check.kind === 'min') result.minLength = check.value;
            if (check.kind === 'max') result.maxLength = check.value;
            if (check.kind === 'regex') result.pattern = check.regex?.source;
            if (check.kind === 'email') result.format = 'email';
            if (check.kind === 'url') result.format = 'uri';
            if (check.kind === 'uuid') result.format = 'uuid';
          }
        }
        return result;
      }
      case 'ZodNumber': {
        const result: any = { type: 'number' };
        if (def.checks) {
          for (const check of def.checks) {
            if (check.kind === 'min') result.minimum = check.value;
            if (check.kind === 'max') result.maximum = check.value;
            if (check.kind === 'int') result.type = 'integer';
          }
        }
        return result;
      }
      case 'ZodBoolean':
        return { type: 'boolean' };
      case 'ZodArray': {
        return {
          type: 'array',
          items: zodToJsonSchema(def.type),
        };
      }
      case 'ZodObject': {
        const shape: Record<string, any> = {};
        const required: string[] = [];
        
        const shapeObj = def.shape();
        for (const [key, value] of Object.entries(shapeObj)) {
          shape[key] = zodToJsonSchema(value as z.ZodSchema<any>);
          // Check if optional
          const valueDef = (value as any)._def;
          if (valueDef && valueDef.typeName !== 'ZodOptional') {
            required.push(key);
          }
        }
        
        return {
          type: 'object',
          properties: shape,
          required,
          additionalProperties: false,
        };
      }
      case 'ZodOptional':
        return zodToJsonSchema(def.innerType);
      case 'ZodNullable':
        return { anyOf: [zodToJsonSchema(def.innerType), { type: 'null' }] };
      case 'ZodDefault':
        return zodToJsonSchema(def.innerType);
      case 'ZodEnum':
        return { type: 'string', enum: def.values };
      case 'ZodNativeEnum':
        return { type: 'string', enum: Object.values(def.values) };
      case 'ZodUnion':
        return { anyOf: def.options.map((opt: any) => zodToJsonSchema(opt)) };
      case 'ZodEffects':
        return zodToJsonSchema(def.schema);
      default:
        return { type: 'string' };
    }
  } catch (e) {
    return { type: 'string' };
  }
}

export function toolDefinitionToOpenAIFunction(tool: any): any {
  return {
    type: 'function',
    function: {
      name: tool.name,
      description: tool.description,
      parameters: zodToJsonSchema(tool.schema),
    },
  };
}