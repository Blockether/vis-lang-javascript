import { Module } from '@nestjs/common';

import { AppController } from './app.controller';
import { GreeterService } from './greeter.service';
import { PollerService } from './poller.service';
import { RequestContext } from './request-context';
import { Stamp } from './stamp';

@Module({
  controllers: [AppController],
  providers: [GreeterService, PollerService, RequestContext, Stamp],
})
export class AppModule {}
