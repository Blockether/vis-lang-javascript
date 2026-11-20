import { NestFactory } from '@nestjs/core';

import { AppModule } from './app.module';

async function bootstrap() {
  const app = await NestFactory.create(AppModule, { logger: ['error', 'warn'] });
  await app.listen(Number(process.env.PORT ?? 0), '127.0.0.1');
  console.log(`listening on ${await app.getUrl()}`);
}
void bootstrap();
